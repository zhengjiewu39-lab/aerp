from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from aerp.core.anomaly import AlertDraft
from aerp.core.models.base import HorizonForecast, PredictResult
from aerp.core.models.ensemble import dynamic_weights
from aerp.services.experiment_engine import _evaluate_forecasts, _predict_arms


def _pred(model_id: str, median: float) -> PredictResult:
    horizons = {}
    for horizon in (1, 2, 3, 4):
        raw = {
            0.025: median - 3,
            0.1: median - 2,
            0.25: median - 1,
            0.5: median,
            0.75: median + 1,
            0.9: median + 2,
            0.975: median + 3,
        }
        horizons[horizon] = HorizonForecast(horizon, raw, "m", "n")
    return PredictResult(model_id, "success", "ok", horizons, {})


def _components():
    return {
        "persistence": {"predict": _pred("persistence", 10)},
        "damped_trend": {"predict": _pred("damped_trend", 12)},
        "retrain_glm": {"predict": _pred("retrain_glm", 8)},
        "fixed_glm": {"predict": _pred("fixed_glm", 9)},
    }


def test_equal_weight_unchanged_when_adaptive_enabled():
    components = _components()
    cutoff = datetime(2024, 10, 9)
    common = dict(
        components=components,
        history_scores=[],
        cutoff=cutoff,
        alerts=[],
        train_frame=pd.DataFrame(),
        config={"mode": "research", "allow_demo_analogs": False},
        ablations={},
    )
    without = _predict_arms(arm_ids=["equal_weight", "dynamic_weight"], **common)
    with_adaptive = _predict_arms(arm_ids=["equal_weight", "dynamic_weight", "adaptive"], **common)
    assert without["equal_weight"].horizons[1].quantiles == with_adaptive["equal_weight"].horizons[1].quantiles
    assert without["dynamic_weight"].horizons[1].quantiles == with_adaptive["dynamic_weight"].horizons[1].quantiles


def test_adaptation_rules_do_not_mutate_controls():
    components = _components()
    original = dict(components["damped_trend"]["predict"].horizons[1].quantiles)
    alert = AlertDraft(
        category="suspected_epidemic_change",
        level="warning",
        rule="growth",
        metrics=["hospital_admissions"],
        period_start="2024-09-01",
        period_end="2024-09-07",
        source_id=None,
        import_batch_id=None,
        supporting_evidence="t",
        conflicting_evidence="",
        limitations="",
    )
    index = pd.date_range("2023-01-07", periods=40, freq="7D")
    train = pd.DataFrame({"period_end": index, "period_start": index - pd.Timedelta(days=6), "value": np.linspace(5, 25, 40)})
    out = _predict_arms(
        components,
        arm_ids=["equal_weight", "adaptive", "damped_trend"],
        history_scores=[],
        cutoff=datetime(2024, 10, 9),
        alerts=[alert],
        train_frame=train,
        config={"mode": "research", "allow_demo_analogs": False, "widen_intervals_on_epidemic": True},
        ablations={},
    )
    assert components["damped_trend"]["predict"].horizons[1].quantiles == original
    assert out["damped_trend"].horizons[1].quantiles == original
    assert out["adaptive"].horizons[1].quantiles[0.975] >= out["equal_weight"].horizons[1].quantiles[0.975]


def test_failed_component_does_not_kill_other_arms():
    components = _components()
    components["retrain_glm"]["predict"] = PredictResult("retrain_glm", "failed", "boom")
    out = _predict_arms(
        components,
        arm_ids=["equal_weight", "persistence", "adaptive"],
        history_scores=[],
        cutoff=datetime(2024, 10, 9),
        alerts=[],
        train_frame=pd.DataFrame(),
        config={"mode": "research"},
        ablations={},
    )
    assert out["equal_weight"].status == "success"
    assert out["persistence"].status == "success"
    assert "retrain_glm" in out["equal_weight"].metadata["excluded"]


def test_matured_errors_change_later_weights_immature_do_not():
    cutoff = datetime(2024, 6, 1)
    immature = [
        {"model_id": "persistence", "wis": 1.0, "matured_at": datetime(2024, 7, 1)},
        {"model_id": "retrain_glm", "wis": 9.0, "matured_at": datetime(2024, 7, 1)},
    ]
    weights, _ = dynamic_weights(immature, ["persistence", "retrain_glm"], cutoff)
    assert abs(weights["persistence"] - weights["retrain_glm"]) < 1e-9
    mature = [
        {"model_id": "persistence", "wis": 1.0, "matured_at": datetime(2024, 5, 1)},
        {"model_id": "retrain_glm", "wis": 9.0, "matured_at": datetime(2024, 5, 1)},
    ]
    shifted, _ = dynamic_weights(mature, ["persistence", "retrain_glm"], cutoff)
    assert shifted["persistence"] > shifted["retrain_glm"]


def test_evaluate_is_idempotent(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from aerp.db.models import Disease, Evaluation, Experiment, Forecast, ModelRun
    from aerp.db.session import Base

    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    db = Session()
    db.add(Disease(id="influenza", name="influenza", description="test", is_synthetic=False))
    experiment = Experiment(
        id="exp-idemp",
        name="t",
        disease_id="influenza",
        region_ids=["01"],
        cutoff_time=datetime(2024, 11, 1),
        horizons=[1],
        models=["persistence"],
        replay_mode="final_time_split",
        maturity_lag_days=7,
        random_seed=1,
        config={},
        created_at=datetime(2024, 1, 1),
        summary={},
        dependency_versions={},
    )
    db.add(experiment)
    db.add(ModelRun(id="exp-idemp-persistence", experiment_id="exp-idemp", model_id="persistence", status="success", metadata_json={}))
    forecast = Forecast(
        experiment_id="exp-idemp",
        model_run_id="exp-idemp-persistence",
        model_id="persistence",
        region_id="01",
        metric="hospital_admissions",
        issued_at=datetime(2024, 10, 1),
        cutoff_time=datetime(2024, 10, 1),
        target_period_start=datetime(2024, 10, 6),
        target_period_end=datetime(2024, 10, 12),
        horizon=1,
        q025=1,
        q10=1,
        q25=1,
        q50=2,
        q75=3,
        q90=3,
        q975=4,
        generated_at=datetime(2024, 10, 1),
        extra={},
        weights={},
    )
    db.add(forecast)
    db.commit()
    obs = pd.DataFrame(
        [
            {
                "disease_id": "influenza",
                "region_id": "01",
                "metric": "hospital_admissions",
                "period_start": datetime(2024, 10, 6),
                "period_end": datetime(2024, 10, 12),
                "issued_at": datetime(2024, 10, 20),
                "value": 2.0,
                "unit": "count",
                "source_id": "t",
                "quality_flag": "observed",
            }
        ]
    )
    first = _evaluate_forecasts(db, experiment=experiment, all_obs=obs, forecasts=[forecast], evaluation_as_of=datetime(2024, 11, 1), purpose="online", write_pending=False)
    db.commit()
    second = _evaluate_forecasts(db, experiment=experiment, all_obs=obs, forecasts=[forecast], evaluation_as_of=datetime(2024, 11, 1), purpose="online", write_pending=False)
    n = db.query(Evaluation).filter(Evaluation.experiment_id == "exp-idemp", Evaluation.purpose == "online").count()
    assert len(first) == 1
    assert second == []
    assert n == 1
    db.close()
