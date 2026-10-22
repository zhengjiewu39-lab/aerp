from __future__ import annotations

import numpy as np
import pandas as pd

from aerp.core.calendar import build_forecast_task, weeks_between
from aerp.core.models.hierarchical import PanelCountModel
from aerp.core.models.persistence import PersistenceModel
from aerp.core.series import frame_from_grid, prepare_weekly, shared_weekly_index


def test_reference_week_is_saturday_and_shared():
    task = build_forecast_task("2024-10-09", [1, 2, 3, 4])  # Wednesday
    assert task.reference_week_end.weekday() == 5
    assert task.reference_week_end == pd.Timestamp("2024-10-05")
    assert task.target_week_end(1) == pd.Timestamp("2024-10-12")
    assert task.target_week_end(2) == pd.Timestamp("2024-10-19")


def test_reference_week_rule_change_shifts_targets():
    cutoff = "2024-10-05"  # Saturday
    default = build_forecast_task(cutoff, [1], rule="saturday_on_or_before_cutoff")
    before = build_forecast_task(cutoff, [1], rule="saturday_before_cutoff")
    assert default.reference_week_end == pd.Timestamp("2024-10-05")
    assert before.reference_week_end == pd.Timestamp("2024-09-28")
    assert default.target_week_end(1) != before.target_week_end(1)


def test_delayed_last_observation_does_not_relabel_target():
    task = build_forecast_task("2024-10-09", [1, 2, 3, 4])
    last_visible = pd.Timestamp("2024-09-28")
    assert task.steps_from(last_visible, 1) == 2
    assert task.target_week_end(1) == pd.Timestamp("2024-10-12")


def test_middle_missing_and_staggered_starts_use_shared_calendar():
    early = pd.date_range("2023-01-07", periods=20, freq="7D")
    late = pd.date_range("2023-02-18", periods=12, freq="7D")
    frame_a = frame_from_grid(early, np.linspace(10, 20, 20))
    values_b = np.linspace(5, 8, 12)
    values_b[3] = np.nan
    frame_b = frame_from_grid(late, values_b)
    index = shared_weekly_index([frame_a, frame_b])
    aligned_a, series_a = prepare_weekly(frame_a, index)
    aligned_b, series_b = prepare_weekly(frame_b, index)
    assert aligned_a.equals(aligned_b)
    assert np.isnan(series_b[list(index).index(late[3])])
    assert np.isnan(series_b[0])


def test_hierarchical_uses_calendar_not_local_length():
    long_index = pd.date_range("2023-01-07", periods=40, freq="7D")
    short_index = pd.date_range("2023-04-01", periods=24, freq="7D")
    long = frame_from_grid(long_index, 20 + np.sin(np.arange(40) / 5.0) * 3)
    short = frame_from_grid(short_index, 8 + np.sin(np.arange(24) / 5.0) * 2)
    long["unit"] = "count"
    short["unit"] = "count"
    model = PanelCountModel("hierarchical")
    fit = model.fit({"long": long, "short": short}, config={"mode": "hierarchical", "dynamic_slope": False})
    assert fit.status == "success", fit.message
    last = max(long_index[-1], short_index[-1])
    task = build_forecast_task(last + pd.Timedelta(days=1), [1, 2], week_grid=list(long_index) + list(short_index))
    pred_long = model.predict_region("long", [1, 2], target_week_ends=task.target_week_ends())
    pred_short = model.predict_region("short", [1, 2], target_week_ends=task.target_week_ends())
    assert pred_long.status == "success"
    assert pred_short.status == "success"


def test_persistence_rejects_target_not_after_last_visible():
    index = pd.date_range("2024-01-06", periods=16, freq="7D")
    frame = frame_from_grid(index, np.full(16, 5.0))
    model = PersistenceModel()
    assert model.fit(frame, {"horizons": [1], "min_obs": 8, "min_residuals": 8}).status == "success"
    bad = {1: index[-1]}
    pred = model.predict([1], target_week_ends=bad)
    assert pred.status == "failed"
