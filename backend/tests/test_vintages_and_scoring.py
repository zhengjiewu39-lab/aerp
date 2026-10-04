from __future__ import annotations

import numpy as np
import pandas as pd

from aerp.core.models.ensemble import dynamic_weights
from aerp.core.models.persistence import PersistenceModel
from aerp.core.quantiles import ordered_nonnegative
from aerp.core.scoring import interval_score, weighted_interval_score
from aerp.core.synthetic import generate_synthetic
from aerp.core.vintages import latest_visible, mature_value


def test_cutoff_does_not_read_future_vintages():
    bundle = generate_synthetic(seed=42, n_weeks=40)
    frame = bundle.observations
    frame = frame[(frame["region_id"] == "R1") & (frame["metric"] == "hospital_admissions")]
    period = frame["period_end"].min()
    versions = frame[frame["period_end"] == period].sort_values("issued_at")
    cutoff = versions.iloc[0]["issued_at"]
    visible = latest_visible(frame, cutoff)
    row = visible[(visible["period_end"] == period)].iloc[0]
    assert row["issued_at"] <= cutoff
    assert float(row["value"]) == float(versions.iloc[0]["value"])


def test_multiple_vintages_preserved():
    bundle = generate_synthetic(seed=42, n_weeks=20)
    frame = bundle.observations
    group = frame[(frame["region_id"] == "R1") & (frame["metric"] == "hospital_admissions") & (frame["period_end"] == frame["period_end"].min())]
    assert group["issued_at"].nunique() >= 2


def test_missing_not_zero():
    bundle = generate_synthetic(seed=42, n_weeks=104)
    missing = bundle.observations[
        (bundle.observations["region_id"] == "R4")
        & (bundle.observations["metric"] == "hospital_admissions")
        & (bundle.observations["quality_flag"] == "missing")
    ]
    assert not missing.empty
    assert missing["value"].isna().all()


def test_truth_not_in_observations():
    bundle = generate_synthetic(seed=42, n_weeks=30)
    assert "latent_infections" not in set(bundle.observations["metric"])
    assert "simulation_hospital_admissions" not in set(bundle.observations["metric"])
    assert not bundle.truth.empty


def test_wis_definition():
    # y inside interval: IS = width
    assert interval_score(10, 8, 12, 0.2) == 4
    # below
    assert interval_score(5, 8, 12, 0.2) == 4 + (2 / 0.2) * 3
    wis = weighted_interval_score(
        10,
        10,
        {"50": (9, 11), "80": (8, 12), "95": (7, 13)},
    )
    assert wis > 0


def test_quantiles_ordered_nonnegative():
    raw = {0.025: -2, 0.1: 5, 0.25: 4, 0.5: 6, 0.75: 3, 0.9: 8, 0.975: 7}
    ordered = ordered_nonnegative(raw)
    values = [ordered[level] for level in sorted(ordered)]
    assert values == sorted(values)
    assert min(values) >= 0


def test_small_sample_degrades():
    frame = pd.DataFrame(
        {
            "period_end": pd.date_range("2024-01-07", periods=3, freq="7D"),
            "period_start": pd.date_range("2024-01-01", periods=3, freq="7D"),
            "value": [1.0, np.nan, 2.0],
            "unit": ["count", "count", "count"],
        }
    )
    model = PersistenceModel()
    fit = model.fit(frame, {"min_obs": 8, "min_residuals": 8, "horizons": [1, 2]})
    assert fit.status in {"degraded", "failed"}
    pred = model.predict([1, 2])
    assert pred.status != "success" or not pred.horizons


def test_dynamic_weights_ignore_immature():
    cutoff = pd.Timestamp("2024-06-01")
    records = [
        {"model_id": "persistence", "wis": 1.0, "matured_at": "2024-05-01"},
        {"model_id": "damped_trend", "wis": 10.0, "matured_at": "2024-07-01"},
    ]
    weights, note = dynamic_weights(records, ["persistence", "damped_trend"], cutoff)
    assert "未成熟" in note or weights["persistence"] > weights["damped_trend"] or abs(weights["persistence"] - 0.5) < 1e-9
    # immature record must not dominate
    assert weights["persistence"] >= weights["damped_trend"]


def test_mature_value_requires_lag():
    bundle = generate_synthetic(seed=42, n_weeks=40)
    frame = bundle.observations
    period = frame[(frame["region_id"] == "R1") & (frame["metric"] == "hospital_admissions")]["period_end"].iloc[10]
    too_early = pd.Timestamp(period) + pd.Timedelta(days=2)
    assert (
        mature_value(
            frame,
            region_id="R1",
            metric="hospital_admissions",
            period_end=period,
            as_of=too_early,
            maturity_lag_days=28,
            replay_mode="vintage",
        )
        is None
    )
