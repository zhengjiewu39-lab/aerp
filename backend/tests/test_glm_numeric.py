from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from aerp.core.models.hierarchical import PanelCountModel
from aerp.core.models.negbin import _fit_poisson_irls, _poisson_ppf, predictive_quantiles
from aerp.core.series import frame_from_grid


def test_intercept_only_poisson_matches_log_mean():
    y = np.full(30, 12.0)
    design = np.ones((30, 1))
    mu, coef, info = _fit_poisson_irls(design, y)
    assert info["converged"] is True
    assert info["rank"] == 1
    assert np.allclose(mu, 12.0, atol=1e-5)
    assert abs(float(coef[0]) - math.log(12.0)) < 1e-5


def test_rank_deficient_design_drops_duplicate_column():
    y = np.arange(10, 20, dtype=float)
    col = np.ones(10)
    design = np.column_stack([col, col])
    _mu, coef, info = _fit_poisson_irls(design, y)
    assert info["n_params"] == 1
    assert info["dropped_columns"] == [1]
    assert coef.shape == (1,)


def test_poisson_ppf_matches_scipy_when_available():
    scipy = pytest.importorskip("scipy")
    from scipy.stats import poisson

    for mu in (1.0, 5.0, 20.0):
        for p in (0.025, 0.5, 0.975):
            got = _poisson_ppf(p, mu)
            expected = float(poisson.ppf(p, mu))
            assert abs(got - expected) <= 1.0


def test_predictive_quantiles_ordered_and_cover_mean():
    quantiles, method = predictive_quantiles(10.0, 0.0)
    seq = [quantiles[level] for level in sorted(quantiles)]
    assert seq == sorted(seq)
    assert quantiles[0.025] <= 10 <= quantiles[0.975]
    assert "poisson" in method


def test_residual_shrinkage_moves_region_toward_grand_mean():
    index = pd.date_range("2023-01-07", periods=40, freq="7D")
    low = frame_from_grid(index, np.full(40, 8.0))
    high = frame_from_grid(index, np.full(40, 40.0))
    low["unit"] = "count"
    high["unit"] = "count"
    model = PanelCountModel("hierarchical")
    fit = model.fit({"low": low, "high": high}, config={"mode": "hierarchical", "dynamic_slope": False})
    assert fit.status == "success", fit.message
    intercepts = model.serialize_metadata()["intercepts"]
    assert intercepts["low"] < 0 < intercepts["high"]
    pooled = PanelCountModel("pooled_glm")
    pooled.fit({"low": low, "high": high}, config={"mode": "pooled_glm", "dynamic_slope": False})
    assert pooled._intercepts["low"] == 0.0
