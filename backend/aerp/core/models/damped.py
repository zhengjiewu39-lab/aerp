from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from aerp.core.models.base import FitResult, HorizonForecast, PredictResult, check_units, covariate_note
from aerp.core.models.persistence import rolling_errors
from aerp.core.quantiles import QUANTILE_LEVELS, empirical_predictive_quantiles
from aerp.core.series import finite_count, last_finite, prepare_weekly


def damped_point(history: np.ndarray, horizon: int, phi: float = 0.75, growth_cap: float = 0.35) -> float:
    last = last_finite(history)
    if last is None:
        raise ValueError("没有可见值")
    last_index, level = last
    start = max(1, last_index - 8)
    diffs = []
    for index in range(start, last_index + 1):
        if np.isfinite(history[index]) and np.isfinite(history[index - 1]):
            diffs.append(float(history[index] - history[index - 1]))
    slope = float(np.median(diffs)) if diffs else 0.0
    cap = growth_cap * max(level, 1.0)
    slope = float(np.clip(slope, -cap, cap))
    if abs(1.0 - phi) < 1e-9:
        increment = slope * horizon
    else:
        increment = slope * (phi * (1.0 - phi**horizon) / (1.0 - phi))
    return max(0.0, level + increment)


class DampedTrendModel:
    """阻尼趋势。增长率有上限，外推随着预测时长衰减。"""

    model_id = "damped_trend"

    def __init__(self) -> None:
        self._values: np.ndarray | None = None
        self._errors: dict[int, np.ndarray] = {}
        self._fit: FitResult | None = None
        self._min_residuals = 8
        self.phi = 0.75
        self.growth_cap = 0.35

    def fit(self, training_data: pd.DataFrame, config: dict | None = None) -> FitResult:
        config = config or {}
        unit_error = check_units(training_data)
        if unit_error:
            self._fit = FitResult("failed", unit_error)
            return self._fit
        _index, values = prepare_weekly(training_data)
        self._values = values
        self.phi = float(config.get("phi", 0.75))
        self.growth_cap = float(config.get("growth_cap", 0.35))
        self._min_residuals = int(config.get("min_residuals", 8))
        minimum = int(config.get("min_obs", 16))
        observed = finite_count(values)
        if observed == 0:
            self._fit = FitResult("degraded", "全部缺失。缺失值没有替换为 0，也没有伪造训练成功。")
            return self._fit
        if observed < minimum:
            self._fit = FitResult("degraded", f"有效样本 {observed} 周，低于 {minimum} 周，未伪造训练成功。")
            return self._fit
        if np.nanmin(values) < 0:
            self._fit = FitResult("failed", "阻尼趋势遇到负的计数，未把负值改成 0 后继续训练。")
            return self._fit
        horizons = [int(item) for item in config.get("horizons", [1, 2, 3, 4])]
        self._errors = rolling_errors(values, self._point, horizons, min_history=6)
        if all(sample.size < self._min_residuals for sample in self._errors.values()):
            self._fit = FitResult("degraded", "滚动误差样本不足，无法按历史误差构建预测区间。")
            return self._fit
        self._fit = FitResult(
            "success",
            "已估计受上限约束的阻尼斜率。",
            {"n_obs": observed, "phi": self.phi, "growth_cap": self.growth_cap},
        )
        return self._fit

    def _point(self, history: np.ndarray, horizon: int) -> float:
        return damped_point(history, horizon, self.phi, self.growth_cap)

    def predict(self, horizons: list[int], available_covariates: dict | None = None) -> PredictResult:
        if self._fit is None or self._fit.status != "success" or self._values is None:
            message = self._fit.message if self._fit else "尚未拟合"
            status = self._fit.status if self._fit is not None else "failed"
            return PredictResult(self.model_id, status, message)
        note = covariate_note(available_covariates)
        output: dict[int, HorizonForecast] = {}
        for horizon in horizons:
            point = self._point(self._values, int(horizon))
            errors = self._errors.get(int(horizon), np.array([]))
            method = "empirical_residual_quantiles"
            if errors.size < self._min_residuals:
                base = self._errors.get(1, np.array([]))
                if base.size < self._min_residuals:
                    return PredictResult(self.model_id, "degraded", "无法为全部预测时长构建误差区间。")
                errors = base * np.sqrt(int(horizon))
                method = "empirical_one_step_residuals_scaled_by_sqrt_h"
            quantiles = empirical_predictive_quantiles(point, errors, QUANTILE_LEVELS, self._min_residuals)
            if quantiles is None:
                return PredictResult(self.model_id, "degraded", "无法为全部预测时长构建误差区间。")
            output[int(horizon)] = HorizonForecast(int(horizon), quantiles, method, note)
        return PredictResult(self.model_id, "success", self._fit.message, output, self.serialize_metadata())

    def diagnostics(self) -> dict[str, Any]:
        return {"model_id": self.model_id, "status": None if self._fit is None else self._fit.status, "phi": self.phi}

    def serialize_metadata(self) -> dict[str, Any]:
        return {
            **self.diagnostics(),
            "assumptions": "短期斜率受限并随预测时长阻尼，区间来自过去的滚动误差。",
            "interval_method": "empirical_residual_quantiles",
        }
