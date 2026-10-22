from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from aerp.core.alerts_eval import independent_joint_exceed_probability, joint_exceed_report
from aerp.core.change_labels import diagnosis_report
from aerp.core.trajectories import sample_copula_trajectories
from aerp.research.archive import archive_forecast, verify_chain
from aerp.research.replay import run_vintage_replay
from aerp.services.delphi import parse_epidata_json, verify_delphi
from aerp.services.flusight import fetch_flusight_target, flu_season_origins, parse_flusight_csv, vintage_issued_at
from aerp.services.flusight_models import parse_hubverse_forecast, quantile_forecasts, sample_tables

FIXTURES = Path(__file__).parent / "fixtures"


def test_as_of_saturday_before_break_shifts_to_wednesday():
    as_of = pd.Series(["2023-10-07", "2023-10-11", "2025-07-05", "2025-07-12"])
    issued, logs = vintage_issued_at(as_of)
    assert issued.iloc[0] == pd.Timestamp("2023-10-04")
    assert issued.iloc[1] == pd.Timestamp("2023-10-11")
    assert issued.iloc[2] == pd.Timestamp("2025-07-05")
    assert issued.iloc[3] == pd.Timestamp("2025-07-12")
    assert any("周三" in item for item in logs)


def test_vintage_parse_keeps_raw_as_of_and_converts_issued_at():
    payload = (
        "as_of,target,target_end_date,location,location_name,observation,weekly_rate\n"
        "2023-10-07,wk inc flu hosp,2023-09-30,01,Alabama,8,0.16\n"
        "2025-07-09,wk inc flu hosp,2025-07-05,01,Alabama,9,0.18\n"
    ).encode()
    parsed = parse_flusight_csv(payload, kind="vintage")
    assert parsed["status"] == "success"
    rows = parsed["rows"]
    converted = rows[rows["as_of_raw"] == pd.Timestamp("2023-10-07")].iloc[0]
    assert converted["issued_at"] == pd.Timestamp("2023-10-04")
    later = rows[rows["as_of_raw"] == pd.Timestamp("2025-07-09")].iloc[0]
    assert later["issued_at"] == pd.Timestamp("2025-07-09")


def test_copula_joint_differs_from_independent_marginal():
    quantiles = {0.025: 1, 0.1: 2, 0.25: 4, 0.5: 10, 0.75: 18, 0.9: 28, 0.975: 40}
    independent = independent_joint_exceed_probability(quantiles, quantiles, 20)
    traj = sample_copula_trajectories({1: quantiles, 2: quantiles}, n_samples=400, rho=0.9, seed=3)
    report = joint_exceed_report(quantiles_h1=quantiles, quantiles_h2=quantiles, threshold=20, trajectories=traj)
    assert report["degraded"] is False
    assert report["method"] == "gaussian_copula_ar1"
    assert abs(report["probability"] - independent) > 0.01
    fallback = joint_exceed_report(quantiles_h1=quantiles, quantiles_h2=quantiles, threshold=20, trajectories=None)
    assert fallback["degraded"] is True
    assert fallback["method"] == "independent_marginal"


def test_change_diagnosis_skipped_without_real_labels():
    skipped = diagnosis_report([], None, source="none", mode="research")
    assert skipped["status"] == "skipped"
    assert skipped["accuracy"] is None
    labels = pd.DataFrame(
        {
            "region_id": ["01"],
            "period_start": [pd.Timestamp("2024-01-01")],
            "period_end": [pd.Timestamp("2024-01-07")],
            "category": ["suspected_reporting_change"],
            "note": "synthetic demo",
        }
    )
    synthetic = diagnosis_report([], labels, source="synthetic", mode="research")
    assert synthetic["status"] == "synthetic_only"
    assert synthetic["accuracy"] is None
    assert "synthetic_metrics" in synthetic


def test_archive_chain_is_local_only(tmp_path, monkeypatch):
    from aerp.research import archive as archive_mod

    monkeypatch.setattr(archive_mod, "RUNS_DIR", tmp_path)
    first = archive_forecast({"cutoff_time": "2024-10-16", "forecasts": [{"q50": 1}]}, archive_dir=tmp_path / "prospective")
    second = archive_forecast({"cutoff_time": "2024-10-23", "forecasts": [{"q50": 2}]}, archive_dir=tmp_path / "prospective")
    assert first["external_timestamp_status"] == "local_only"
    assert second["status"] == "saved"
    chain = verify_chain(tmp_path / "prospective")
    assert chain["status"] == "ok"
    assert chain["n"] == 2
    assert "不能证明独立公开发布" in chain["note"]
    stamp = Path(first["stamp_request"])
    assert stamp.exists()
    payload = stamp.read_text(encoding="utf-8")
    assert "RFC 3161" in payload


def test_delphi_fixture_parses_issue_not_download_time(tmp_path):
    payload = (FIXTURES / "delphi_nhsn.json").read_bytes()
    parsed = parse_epidata_json(payload, source_id="covidcast_nhsn_flu_week")
    assert parsed["status"] == "success"
    assert parsed["n_rows"] == 2
    assert parsed["rows"]["issued_at"].min() == pd.Timestamp("2024-01-10")
    weekly = parse_epidata_json(
        {
            "result": 1,
            "epidata": [{"geo_value": "pa", "time_value": 202440, "value": 10, "issue": 202441}],
        },
        source_id="covidcast_nhsn_flu_week",
    )
    assert weekly["status"] == "success"
    assert weekly["n_rows"] == 1
    assert weekly["rows"]["period_end"].iloc[0].dayofweek == 5
    verified = verify_delphi(payload_by_id={"covidcast_nhsn_flu_week": payload}, cache_dir=tmp_path)
    assert verified["status"] == "verified"
    assert verified["verified_path"] == "covidcast_nhsn_flu_week"


def test_hubverse_forecast_parses_quantiles_and_samples():
    payload = (FIXTURES / "flusight_hub_forecast.csv").read_bytes()
    parsed = parse_hubverse_forecast(payload, model_id="FluSight-baseline")
    assert parsed["status"] == "success"
    quant = quantile_forecasts(parsed)
    assert len(quant) == 2
    assert quant.iloc[0]["q50"] == 10
    tables = sample_tables(parsed)
    assert ("FluSight-baseline", "06") in tables
    assert tables[("FluSight-baseline", "06")]["status"] == "success"
    assert tables[("FluSight-baseline", "06")]["n_samples"] == 2


def test_offline_vintage_replay_does_not_invent_scores(tmp_path):
    weeks = pd.date_range("2023-10-07", periods=16, freq="7D")
    lines = ["as_of,target,target_end_date,location,location_name,observation,weekly_rate"]
    for index, week in enumerate(weeks):
        as_of = (week + pd.Timedelta(days=4)).date().isoformat()
        lines.append(f"{as_of},wk inc flu hosp,{week.date().isoformat()},01,Alabama,{10 + index},0.2")
        later = (week + pd.Timedelta(days=11)).date().isoformat()
        lines.append(f"{later},wk inc flu hosp,{week.date().isoformat()},01,Alabama,{12 + index},0.24")
    csv_path = tmp_path / "vintage.csv"
    csv_path.write_text("\n".join(lines), encoding="utf-8")
    result = run_vintage_replay(
        kind="vintage",
        file=csv_path,
        locations=["01"],
        models=["persistence"],
        seed=42,
        step_weeks=4,
        min_train_weeks=4,
        max_origins=2,
        include_baselines=False,
        cache_dir=tmp_path / "cache",
    )
    assert result["status"] in {"success", "completed"}
    summary = result["result"]
    assert summary["paper_conclusion"] is None
    assert summary["change_diagnosis"]["status"] == "skipped"
    assert summary["change_diagnosis"]["accuracy"] is None
    assert summary["n_origins"] == 2


def test_flu_season_origins_use_wednesday_after_saturday():
    weeks = pd.date_range("2023-10-07", periods=40, freq="7D")
    origins = flu_season_origins(weeks, min_train_weeks=4, step_weeks=4)
    assert origins
    assert all(item.dayofweek == 2 for item in origins)
    assert all(item.month not in {6, 7, 8, 9} for item in origins)
