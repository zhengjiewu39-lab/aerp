from __future__ import annotations

import numpy as np
import pandas as pd

from aerp.core.models.hierarchical import PanelCountModel
from aerp.core.series import frame_from_grid
from aerp.core.synthetic import generate_synthetic


def _panel_from_bundle(bundle, weeks=48):
    panels = {}
    contexts = {}
    for spec in bundle.regions:
        region_id = spec["id"]
        subset = bundle.observations[
            (bundle.observations["region_id"] == region_id)
            & (bundle.observations["metric"] == "hospital_admissions")
        ]
        latest = subset.sort_values("issued_at").drop_duplicates("period_end", keep="last")
        latest = latest.sort_values("period_end").head(weeks)
        panels[region_id] = latest[["period_start", "period_end", "value", "unit"]].copy()
        if "unit" not in panels[region_id].columns:
            panels[region_id]["unit"] = "count"
        contexts[region_id] = spec.get("context") or {}
    return panels, contexts


def test_pooled_and_hierarchical_quantiles_ordered():
    bundle = generate_synthetic(seed=42, n_weeks=60)
    panels, contexts = _panel_from_bundle(bundle, weeks=48)
    model = PanelCountModel("hierarchical")
    fit = model.fit(panels, contexts=contexts, config={"mode": "hierarchical", "dynamic_slope": False})
    assert fit.status == "success", fit.message
    predicted = model.predict_region("R1", [1, 2, 3, 4], contexts["R1"])
    assert predicted.status == "success", predicted.message
    for item in predicted.horizons.values():
        seq = [item.quantiles[level] for level in sorted(item.quantiles)]
        assert seq == sorted(seq)
        assert all(value >= 0 for value in seq)


def test_holdout_region_does_not_use_own_intercept():
    bundle = generate_synthetic(seed=42, n_weeks=60)
    panels, contexts = _panel_from_bundle(bundle, weeks=48)
    model = PanelCountModel("hierarchical")
    fit = model.fit(
        panels,
        contexts=contexts,
        config={"mode": "hierarchical", "holdout_region_ids": ["R5"], "dynamic_slope": False},
    )
    assert fit.status == "success", fit.message
    assert "R5" in (fit.metadata or {}).get("holdout_region_ids", [])
    predicted = model.predict_region("R5", [1], contexts["R5"])
    assert predicted.metadata.get("holdout") is True
    assert abs(float(predicted.metadata.get("intercept") or 0.0)) < 1e-9


def test_missing_covariate_does_not_fill_zero_as_truth():
    index = pd.date_range("2023-01-08", periods=40, freq="7D")
    values = 20 + 4 * np.sin(np.arange(40) / 6.0)
    frame = frame_from_grid(index, values)
    frame["unit"] = "count"
    model = PanelCountModel("pooled_glm")
    fit = model.fit({"R1": frame, "R2": frame.copy()}, contexts={"R1": {}, "R2": {}}, config={"mode": "pooled_glm"})
    assert fit.status in {"success", "degraded"}
    if fit.status == "success":
        assert "age_share_65" not in (fit.metadata or {}).get("used_features", [])


def test_synthetic_regions_have_demographics_and_aux():
    bundle = generate_synthetic(seed=42, n_weeks=40)
    assert {item["id"] for item in bundle.regions} >= {"R1", "R2", "R3", "R4", "R5"}
    for spec in bundle.regions:
        context = spec["context"]
        assert context.get("country_id")
        assert context.get("age_distribution")
        assert context.get("ethnicity_analysis") == "skipped_no_data"
        assert "household_size" in context
    metrics = set(bundle.observations["metric"])
    assert {"mobility_index", "vaccination_coverage", "prior_infection_estimate", "school_in_session"} <= metrics
    missing_vax = bundle.observations[
        (bundle.observations["metric"] == "vaccination_coverage") & (bundle.observations["value"].isna())
    ]
    assert not missing_vax.empty
    assert (missing_vax["quality_flag"] == "missing").all()
