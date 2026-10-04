from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from aerp.core.models.ensemble import combine_predictions, dynamic_weights
from aerp.core.models.persistence import PersistenceModel
from aerp.core.quantiles import ordered_nonnegative
from aerp.core.scoring import weighted_interval_score
from aerp.core.synthetic import generate_synthetic
from aerp.core.vintages import latest_visible, mature_value


def test_cutoff_excludes_future_vintages():
    bundle = generate_synthetic(seed=42, n_weeks=40)
    frame = bundle.observations
    cutoff = pd.Timestamp("2023-06-01")
    visible = latest_visible(frame, cutoff)
    assert visible["issued_at"].max() <= cutoff
    assert visible["period_end"].max() <= cutoff


def test_multiple_vintages_preserved():
    bundle = generate_synthetic(seed=42, n_weeks=20)
    subset = bundle.observations[
        (bundle.observations["region_id"] == "R1")
        & (bundle.observations["metric"] == "hospital_admissions")
        & (bundle.observations["period_end"] == bundle.observations["period_end"].min())
    ]
    assert subset["issued_at"].nunique() >= 2


def test_missing_not_zero():
    bundle = generate_synthetic(seed=42, n_weeks=104)
    missing = bundle.observations[
        (bundle.observations["region_id"] == "R4")
        & (bundle.observations["metric"] == "hospital_admissions")
        & (bundle.observations["value"].isna())
    ]
    assert not missing.empty
    assert not (missing["value"].fillna(0).eq(0) & missing["quality_flag"].ne("missing")).any()


def test_truth_not_in_observations():
    bundle = generate_synthetic(seed=42, n_weeks=30)
    assert "latent_infections" not in set(bundle.observations["metric"])
    assert set(bundle.truth["metric"]) >= {"latent_infections", "simulation_hospital_admissions"}


def test_wis_and_coverage_helpers():
    score = weighted_interval_score(10, 10, {"50": (9, 11), "80": (8, 12), "95": (7, 13)})
    assert score >= 0
    outside = weighted_interval_score(20, 10, {"50": (9, 11), "80": (8, 12), "95": (7, 13)})
    assert outside > score


def test_quantiles_ordered_nonnegative():
    values = ordered_nonnegative({0.1: -2, 0.5: 3, 0.9: 1})
    assert list(values.values()) == sorted(values.values())
    assert all(value >= 0 for value in values.values())


def test_persistence_degrades_on_all_missing():
    frame = pd.DataFrame(
        {
            "period_end": pd.date_range("2024-01-07", periods=12, freq="7D"),
            "period_start": pd.date_range("2024-01-01", periods=12, freq="7D"),
            "value": [np.nan] * 12,
            "unit": ["count"] * 12,
        }
    )
    model = PersistenceModel()
    fit = model.fit(frame)
    assert fit.status == "degraded"
    pred = model.predict([1, 2])
    assert pred.status == "degraded"


def test_dynamic_weights_ignore_immature():
    cutoff = datetime(2024, 6, 1)
    records = [
        {"model_id": "a", "wis": 1.0, "matured_at": datetime(2024, 5, 1)},
        {"model_id": "b", "wis": 4.0, "matured_at": datetime(2024, 5, 1)},
        {"model_id": "a", "wis": 0.1, "matured_at": datetime(2024, 7, 1)},
    ]
    weights, note = dynamic_weights(records, ["a", "b"], cutoff)
    assert weights["a"] > weights["b"]
    assert "未成熟" in note or "成熟" in note


def test_ensemble_survives_failed_component():
    from aerp.core.models.base import HorizonForecast, PredictResult

    ok = PredictResult(
        "persistence",
        "success",
        "ok",
        {1: HorizonForecast(1, {0.025: 1, 0.1: 1, 0.25: 1, 0.5: 2, 0.75: 3, 0.9: 3, 0.975: 4}, "m", "n")},
    )
    bad = PredictResult("damped_trend", "failed", "boom")
    combined = combine_predictions([ok, bad], {"persistence": 0.5, "damped_trend": 0.5}, model_id="equal_weight", note="t")
    assert combined.status == "success"
    assert "damped_trend" in combined.metadata["excluded"]


def test_mature_value_respects_lag():
    bundle = generate_synthetic(seed=42, n_weeks=40)
    period = bundle.observations["period_end"].min()
    early = mature_value(
        bundle.observations,
        region_id="R1",
        metric="hospital_admissions",
        period_end=period,
        as_of=pd.Timestamp(period) + pd.Timedelta(days=2),
        maturity_lag_days=28,
        replay_mode="vintage",
    )
    later = mature_value(
        bundle.observations,
        region_id="R1",
        metric="hospital_admissions",
        period_end=period,
        as_of=pd.Timestamp(period) + pd.Timedelta(days=30),
        maturity_lag_days=28,
        replay_mode="vintage",
    )
    assert early is None
    assert later is not None
