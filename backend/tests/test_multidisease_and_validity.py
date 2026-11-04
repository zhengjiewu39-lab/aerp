from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aerp.core.alerts_eval import development_thresholds, event_lead_time, freeze_one_vintage
from aerp.core.diseases import get_disease, list_disease_specs
from aerp.core.models.negbin import _fit_poisson_irls, _nbinom_ppf
from aerp.core.models.transfer import exit_weight, scale_source_to_target
from aerp.core.quantiles import mix_predictive_cdfs, mix_quantile_average
from aerp.core.recommend import recommend_models
from aerp.core.reliability import reliability_evidence
from aerp.research.splits import assign_split_set, infer_splits
from aerp.research.transfer_run import generate_paired_respiratory, run_transfer_comparison
from aerp.services.delphi import parse_epidata_json
from aerp.services.dengue import check_dengue_sources
from aerp.services.flusight_models import paired_common_tasks

FIXTURES = Path(__file__).parent / "fixtures"


def test_intercept_only_with_offset_recovers_mean_and_intercept():
    y = np.full(40, 12.0)
    design = np.ones((40, 1))
    offset = np.full(40, math.log(2.0))
    mu, coef, info = _fit_poisson_irls(design, y, offset=offset)
    assert info["converged"] is True
    assert np.allclose(mu, 12.0, atol=1e-3)
    assert abs(float(coef[0]) - math.log(6.0)) < 1e-3


def test_cdf_mix_differs_from_quantile_average():
    left = {0.025: 0.0, 0.5: 1.0, 0.975: 2.0}
    right = {0.025: 10.0, 0.5: 20.0, 0.975: 30.0}
    averaged = mix_quantile_average([left, right])
    mixed = mix_predictive_cdfs([left, right])
    assert averaged[0.5] == pytest.approx(10.5, abs=0.2)
    assert mixed[0.5] != pytest.approx(averaged[0.5], abs=0.05)


def test_freeze_one_vintage_ignores_later_revisions():
    frame = pd.DataFrame(
        {
            "region_id": ["A", "A", "A"],
            "period_end": pd.to_datetime(["2024-01-06", "2024-01-06", "2024-01-13"]),
            "issued_at": pd.to_datetime(["2024-01-08", "2024-02-01", "2024-01-15"]),
            "value": [10.0, 99.0, 12.0],
        }
    )
    frozen = freeze_one_vintage(frame, freeze_time="2024-01-20")
    first = frozen.loc[frozen["period_end"] == pd.Timestamp("2024-01-06")].iloc[0]
    assert first["value"] == 10.0
    thresholds = development_thresholds(frame, freeze_time="2024-01-20")
    assert "A" not in thresholds or thresholds["A"] != 99.0


def test_assign_split_set_uses_locked_test():
    splits = {
        "development": {"start": "2022-01-01", "end": "2022-12-31"},
        "validation": {"start": "2023-01-07", "end": "2023-12-30"},
        "locked_test": {"start": "2024-01-06", "end": "2024-06-29"},
    }
    assert assign_split_set("2024-03-02", splits) == "locked_test"
    assert assign_split_set("2022-06-01", splits) == "development"
    assert assign_split_set("2025-01-01", splits) == "unspecified"


def test_alert_lead_time_ignores_year_old_alerts():
    truth = [(pd.Timestamp("2024-12-07"), False), (pd.Timestamp("2024-12-14"), True)]
    flags = [(pd.Timestamp("2023-12-16"), True), (pd.Timestamp("2024-11-16"), True)]
    leads = event_lead_time(flags, truth)
    assert leads[0]["detected"] is True
    assert leads[0]["lead_weeks"] == 4


def test_nbinom_ppf_matches_scipy_when_available():
    scipy = pytest.importorskip("scipy")
    from scipy.stats import nbinom

    mean, alpha = 12.0, 0.2
    n = 1.0 / alpha
    p = n / (n + mean)
    for level in (0.1, 0.5, 0.9):
        got = _nbinom_ppf(level, mean, alpha)
        expected = float(nbinom.ppf(level, n, p))
        assert abs(got - expected) <= 1.0


def test_covid_and_rsv_signals_are_not_relabeled_flu():
    covid = parse_epidata_json((FIXTURES / "delphi_covid.json").read_bytes(), source_id="covid_fix", disease_id="covid19")
    rsv = parse_epidata_json((FIXTURES / "delphi_rsv.json").read_bytes(), source_id="rsv_fix", disease_id="rsv")
    assert covid["status"] == "success"
    assert set(covid["rows"]["disease_id"]) == {"covid19"}
    assert set(rsv["rows"]["disease_id"]) == {"rsv"}
    assert "influenza" not in set(covid["rows"]["disease_id"]) | set(rsv["rows"]["disease_id"])


def test_disease_specs_keep_four_statuses():
    specs = {item["disease_id"]: item for item in list_disease_specs()}
    assert specs["influenza"]["status"] in {"verified", "real_forecastable"}
    assert specs["measles"]["status"] == "demo_only"
    assert specs["dengue"]["status"] == "pending"
    assert specs["smallpox"]["trainable"] is False
    assert get_disease("covid19").default_target == "weekly_cases"


def test_transfer_excludes_target_future_and_compares_three_arms():
    frames = generate_paired_respiratory()
    result = run_transfer_comparison(
        frames["synth-covid-hosp"],
        {"synth-flu-hosp": frames["synth-flu-hosp"], "synth-covid-hosp": frames["synth-covid-hosp"]},
        target_disease_id="synth-covid-hosp",
        region_id="PA",
        exclude_target_from_training=True,
    )
    assert result["status"] == "success"
    assert result["data_mode"] == "synthetic_stress_test"
    assert "transfer_local" in result["models"]
    assert "transfer_fixed" in result["models"]
    assert "transfer_exit" in result["models"]
    assert result["models"]["transfer_local"]["n"] > 0
    assert scale_source_to_target(10, source_scale=2, target_scale=4) == 20
    exit_info = exit_weight(
        [
            {"model_id": "local", "wis": 1, "status": "scored", "cutoff_time": f"o{i}"}
            for i in range(6)
        ]
        + [
            {"model_id": "transfer_fixed", "wis": 5, "status": "scored", "cutoff_time": f"o{i}"}
            for i in range(6)
        ]
    )
    assert exit_info["exited"] is True


def test_reliability_does_not_invent_accuracy_percent():
    evidence = reliability_evidence(train_frame=pd.DataFrame({"value": [1, 2, None]}), degraded=True)
    assert evidence["composite_accuracy_percent"] is None
    assert "fallback_baseline" in evidence["degraded_or_exit"]["actions"]


def test_recommend_does_not_claim_best_model():
    rec = recommend_models("dengue", n_weeks=40, has_climate=False)
    assert rec["must_compare_baselines"] is True
    assert "persistence" in rec["candidates"]
    assert "气候" in " ".join(rec["reasons"]) or "媒介" in " ".join(rec["reasons"])


def test_dengue_adapter_does_not_fabricate_series():
    result = check_dengue_sources(live=False)
    assert result["trainable"] is False
    assert result["status"] == "pending"


def test_paired_common_tasks_refuse_date_substitution():
    internal = [{"region_id": "06", "target_period_end": "2024-10-19", "horizon": 1, "wis": 10}]
    external = [{"region_id": "06", "target_period_end": "2024-10-26", "horizon": 1, "wis": 3}]
    result = paired_common_tasks(internal, external)
    assert result["n_common"] == 0
    assert result["status"] == "incomplete"


def test_infer_splits_writes_locked_test_on_long_span():
    ends = pd.date_range("2022-01-08", periods=160, freq="7D")
    frame = pd.DataFrame({"period_end": ends, "metric": "hospital_admissions"})
    result = infer_splits(frame)
    assert "locked_test" in result["splits"]
