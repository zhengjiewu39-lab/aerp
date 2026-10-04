from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from aerp.core.models.base import FitResult, HorizonForecast, PredictResult, check_units, covariate_note
from aerp.core.quantiles import QUANTILE_LEVELS, empirical_predictive_quantiles
from aerp.core.series import finite_count, last_finite, prepare_weekly


def rolling_errors(values: np.ndarray, point_fn, horizons: list[int], min_history: int) -> dict[int, np.ndarray]:
    errors = {horizon: [] for horizon in horizons}
    for origin in range(len(values)):
        history = values[: origin + 1]
        if finite_count(history) < min_history:
            continue
        for horizon in horizons:
            target = origin + horizon
            if target >= len(values) or not np.isfinite(values[target]):
                continue
            try:
                predicted = float(point_fn(history, horizon))
            except Exception:
                continue
            errors[horizon].append(float(values[target]) - predicted)
    return {horizon: np.asarray(sample, dtype=float) for horizon, sample in errors.items()}


class PersistenceModel:
    """未来各周等于最近一个可见值。"""

    model_id = "persistence"

    def __init__(self) -> None:
        self._values: np.ndarray | None = None
        self._errors: dict[int, np.ndarray] = {}
        self._fit: FitResult | None = None
        self._min_residuals = 8
        self._trailing_gap = False

    def fit(self, training_data: pd.DataFrame, config: dict | None = None) -> FitResult:
        config = config or {}
        unit_error = check_units(training_data)
        if unit_error:
            self._fit = FitResult("failed", unit_error)
            return self._fit
        _index, values = prepare_weekly(training_data)
        self._values = values
        minimum = int(config.get("min_obs", 12))
        self._min_residuals = int(config.get("min_residuals", 8))
        observed = finite_count(values)
        if observed == 0:
            self._fit = FitResult("degraded", "全部缺失。缺失值没有替换为 0，也没有伪造训练成功。")
            return self._fit
        if observed < minimum:
            self._fit = FitResult("degraded", f"有效样本 {observed} 周，低于 {minimum} 周，未伪造训练成功。")
            return self._fit
        last = last_finite(values)
        self._trailing_gap = bool(last and last[0] < len(values) - 1)
        horizons = [int(h) for h in config.get("horizons", [1, 2, 3, 4])]
        self._errors = rolling_errors(values, self._point, horizons, min_history=4)
        if all(sample.size < self._min_residuals for sample in self._errors.values()):
            self._fit = FitResult("degraded", "滚动误差样本不足，无法按历史误差构建预测区间。")
            return self._fit
        self._fit = FitResult("success", "已使用最近一个非缺失可见值。", {"n_obs": observed})
        return self._fit

    def _point(self, history: np.ndarray, horizon: int) -> float:
        del horizon
        last = last_finite(history)
        if last is None:
            raise ValueError("没有可见值")
        return last[1]

    def predict(self, horizons: list[int], available_covariates: dict | None = None) -> PredictResult:
        if self._fit is None or self._fit.status != "success" or self._values is None:
            message = self._fit.message if self._fit else "尚未拟合"
            status = self._fit.status if self._fit is not None else "failed"
            return PredictResult(self.model_id, status, message)
        note = covariate_note(available_covariates)
        if self._trailing_gap:
            note += " 序列末端有缺失，沿用更早的可见值，缺失未记为 0。"
        output: dict[int, HorizonForecast] = {}
        for horizon in horizons:
            point = self._point(self._values, horizon)
            quantiles, method = self._quantiles_for(point, int(horizon), horizons)
            if quantiles is None:
                return PredictResult(self.model_id, "degraded", "无法为全部预测时长构建误差区间。")
            output[int(horizon)] = HorizonForecast(int(horizon), quantiles, method, note)
        return PredictResult(self.model_id, "success", self._fit.message, output, self.serialize_metadata())

    def _quantiles_for(self, point: float, horizon: int, horizons: list[int]):
        errors = self._errors.get(horizon, np.array([]))
        method = "empirical_residual_quantiles"
        if errors.size < self._min_residuals:
            base = self._errors.get(1, np.array([]))
            if base.size < self._min_residuals:
                return None, method
            errors = base * np.sqrt(horizon)
            method = "empirical_one_step_residuals_scaled_by_sqrt_h"
        quantiles = empirical_predictive_quantiles(point, errors, QUANTILE_LEVELS, self._min_residuals)
        return quantiles, method

    def diagnostics(self) -> dict[str, Any]:
        last = last_finite(self._values) if self._values is not None else None
        return {"model_id": self.model_id, "last_value": None if last is None else last[1], "status": None if self._fit is None else self._fit.status}

    def serialize_metadata(self) -> dict[str, Any]:
        return {
            **self.diagnostics(),
            "assumptions": "未来各周等于最近一个非缺失可见值。",
            "interval_method": "empirical_residual_quantiles",
        }
