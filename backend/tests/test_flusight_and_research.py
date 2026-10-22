from __future__ import annotations

from pathlib import Path

from aerp.core.alerts_eval import brier_score, development_thresholds, joint_exceed_probability
from aerp.core.research_mode import analog_allowed_in_forecasts, context_interval_scale_allowed
from aerp.core.revision import apply_nowcast, sample_revised_frames
from aerp.research.splits import infer_splits
from aerp.services.flusight import fetch_flusight_target, parse_flusight_csv
import numpy as np
import pandas as pd

FIXTURES = Path(__file__).parent / "fixtures"


def test_snapshot_is_final_time_split_not_download_time():
    payload = (FIXTURES / "flusight_snapshot.csv").read_bytes()
    parsed = parse_flusight_csv(payload, kind="snapshot")
    assert parsed["status"] == "success"
    assert parsed["replay_mode"] == "final_time_split"
    rows = parsed["rows"]
    assert (rows["issued_at"] == rows["period_end"]).all()
    missing = rows[rows["quality_flag"] == "missing"]
    assert not missing.empty
    assert missing["value"].isna().all()


def test_vintage_uses_as_of_not_download_time():
    payload = (FIXTURES / "flusight_vintage.csv").read_bytes()
    parsed = parse_flusight_csv(payload, kind="vintage")
    assert parsed["status"] == "success"
    assert parsed["replay_mode"] == "vintage"
    assert parsed["rows"]["issued_at"].min() == pd.Timestamp("2023-10-11")


def test_offline_fetch_from_payload_is_idempotent(tmp_path):
    payload = (FIXTURES / "flusight_snapshot.csv").read_bytes()
    first = fetch_flusight_target(kind="snapshot", cache_dir=tmp_path, payload=payload)
    second = fetch_flusight_target(kind="snapshot", cache_dir=tmp_path, payload=payload)
    assert first["status"] == second["status"] == "success"
    assert first["provenance"]["sha256"] == second["provenance"]["sha256"]
    csvs = list(tmp_path.glob("snapshot-*.csv"))
    assert len(csvs) == 1


def test_research_mode_disables_demo_analogs_and_subjective_intervals():
    config = {"mode": "research"}
    assert analog_allowed_in_forecasts(config) is False
    assert context_interval_scale_allowed(config) is False
    assert analog_allowed_in_forecasts({"mode": "demo"}) is True


def test_zero_first_report_uses_additive_revision():
    frame = pd.DataFrame(
        {
            "period_end": pd.to_datetime(["2024-01-06", "2024-01-13"]),
            "issued_at": pd.to_datetime(["2024-01-07", "2024-01-14"]),
            "value": [0.0, 4.0],
        }
    )
    profile = {"estimable": True, "median_relative": 0.5, "median_delta": 3.0}
    out = apply_nowcast(frame, "2024-01-20", profile, maturity_lag_days=28, recent_weeks=4)
    assert float(out.loc[0, "value"]) == 3.0


def test_revision_sampling_allows_negative_and_is_reproducible():
    periods = pd.date_range("2023-01-07", periods=12, freq="7D")
    frame = pd.DataFrame(
        {
            "period_end": periods,
            "issued_at": periods + pd.Timedelta(days=1),
            "value": np.linspace(2, 10, 12),
        }
    )
    profile = {
        "estimable": True,
        "paths": [
            {"points": [{"value": 10}, {"value": 8}]} for _ in range(10)
        ],
    }
    first = sample_revised_frames(frame, periods[-1], profile, n_samples=5, seed=7, recent_weeks=4)
    second = sample_revised_frames(frame, periods[-1], profile, n_samples=5, seed=7, recent_weeks=4)
    assert first["status"] == "success"
    assert first["samples"][0]["value"].tolist() == second["samples"][0]["value"].tolist()
    assert any(float(first["samples"][0].iloc[i]["value"]) <= float(frame.iloc[i]["value"]) for i in range(len(frame)))


def test_splits_depend_on_available_span():
    short = pd.DataFrame(
        {
            "metric": ["hospital_admissions"] * 8,
            "period_end": pd.date_range("2024-01-06", periods=8, freq="7D"),
        }
    )
    result = infer_splits(short)
    assert result["status"] == "degraded"
    assert "locked_test" not in result["splits"]


def test_alert_metrics_do_not_claim_capacity():
    from aerp.core.alerts_eval import EVENT_DEFINITION

    assert "不称为容量超载预测" in EVENT_DEFINITION
    quantiles = {0.025: 1, 0.1: 2, 0.25: 3, 0.5: 5, 0.75: 8, 0.9: 12, 0.975: 20}
    prob = joint_exceed_probability(quantiles, quantiles, 10)
    assert 0 < prob < 1
    assert brier_score(np.array([0.2, 0.8]), np.array([0.0, 1.0])) < 0.2
    thresholds = development_thresholds(pd.DataFrame({"region_id": ["A"] * 20, "value": np.arange(20)}))
    assert "A" in thresholds
