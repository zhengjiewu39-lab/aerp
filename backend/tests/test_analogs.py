from __future__ import annotations

import numpy as np
import pandas as pd

from aerp.core.analogs import analog_library, match_values
from aerp.core.context import context_similarity, interval_scale_for_context
from aerp.core.models.analog import AnalogShapeModel
from aerp.core.series import frame_from_grid


def test_analog_shapes_are_normalized_and_nonnegative():
    for item in analog_library():
        shape = np.asarray(item["shape"], dtype=float)
        assert shape.size >= 24
        assert np.all(shape >= 0)
        assert abs(float(shape.max()) - 1.0) < 1e-6
        assert item["trainable_series"] is False


def test_unimodal_series_matches_control_or_sars():
    week = np.arange(40)
    values = 90 * np.exp(-0.5 * ((week - 16) / 5.0) ** 2)
    result = match_values(values)
    assert result["status"] == "success"
    top = result["top"]
    assert top["analog_id"] in {"sars_short_unimodal", "smallpox_unimodal_control"}
    assert top["continuation"]
    assert all(point >= 0 for point in top["continuation"])


def test_level_shift_prefers_reporting_jump():
    values = np.concatenate([np.full(12, 18.0), np.full(16, 64.0)])
    result = match_values(values, window=20)
    assert result["top"]["analog_id"] == "reporting_jump_not_growth"


def test_context_similarity_and_interval_scale():
    same = {"climate_zone": "temperate", "setting": "urban", "healthcare_capacity": "high"}
    other = {"climate_zone": "tropical", "setting": "rural", "healthcare_capacity": "low"}
    assert context_similarity(same, same) == 1.0
    assert context_similarity(same, other) < 0.5
    scale, note = interval_scale_for_context(other)
    assert scale > 1.0
    assert note


def test_analog_model_quantiles_ordered():
    index = pd.date_range("2023-01-08", periods=48, freq="7D")
    week = np.arange(48)
    values = 40 + 35 * (0.5 + 0.5 * np.sin(2 * np.pi * (week - 12) / 52.0))
    frame = frame_from_grid(index, values)
    frame["unit"] = "count"
    model = AnalogShapeModel()
    fit = model.fit(frame, {"horizons": [1, 2, 3, 4], "context": {"climate_zone": "temperate", "setting": "urban"}})
    assert fit.status == "success", fit.message
    predicted = model.predict([1, 2, 3, 4])
    assert predicted.status == "success", predicted.message
    for horizon in predicted.horizons.values():
        levels = sorted(horizon.quantiles)
        seq = [horizon.quantiles[level] for level in levels]
        assert seq == sorted(seq)
        assert all(value >= 0 for value in seq)


def test_analog_match_uses_only_provided_history():
    values = np.concatenate([np.full(20, 12.0), np.linspace(12, 80, 16)])
    early = match_values(values[:20])
    late = match_values(values)
    assert early["top"]["analog_id"] != late["top"]["analog_id"] or early["top"]["direction"] != late["top"]["direction"]
