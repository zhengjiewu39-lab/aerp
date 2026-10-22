from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from aerp.core.analogs import ANALOG_DISCLAIMER, analog_blend_weight, match_values
from aerp.core.calendar import last_visible_week_end, weeks_between
from aerp.core.models.base import FitResult, HorizonForecast, PredictResult, check_units, covariate_note
from aerp.core.models.persistence import rolling_errors
from aerp.core.quantiles import QUANTILE_LEVELS, empirical_predictive_quantiles
from aerp.core.series import finite_count, last_finite, prepare_weekly


class AnalogShapeModel:
    """把当前序列对齐到历史定性形态或对照地区，再按当前水平缩放未来 1—4 周。"""

    model_id = "analog"

    def __init__(self) -> None:
        self._values: np.ndarray | None = None
        self._index: pd.DatetimeIndex | None = None
        self._errors: dict[int, np.ndarray] = {}
        self._fit: FitResult | None = None
        self._match: dict[str, Any] | None = None
        self._min_residuals = 6
        self._context: dict | None = None
        self._peers: dict[str, dict] | None = None
        self._horizons = [1, 2, 3, 4]

    def fit(self, training_data: pd.DataFrame, config: dict | None = None) -> FitResult:
        config = config or {}
        unit_error = check_units(training_data)
        if unit_error:
            self._fit = FitResult("failed", unit_error)
            return self._fit
        index, values = prepare_weekly(training_data)
        self._index = index
        self._values = values
        self._context = config.get("context")
        self._peers = config.get("peers") or {}
        self._horizons = [int(item) for item in config.get("horizons", [1, 2, 3, 4])]
        self._min_residuals = int(config.get("min_residuals", 6))
        observed = finite_count(values)
        if observed < 12:
            self._fit = FitResult("degraded", f"有效样本 {observed} 周，不足以做形态匹配。缺失未记为 0。")
            return self._fit
        self._match = match_values(
            values,
            context=self._context,
            peers=self._peers,
            horizons=self._horizons,
        )
        top = (self._match or {}).get("top")
        if self._match is None or self._match.get("status") != "success" or top is None:
            self._fit = FitResult("degraded", (self._match or {}).get("message") or "形态匹配失败。")
            return self._fit
        self._errors = rolling_errors(values, self._point, self._horizons, min_history=12)
        if all(sample.size < self._min_residuals for sample in self._errors.values()):
            self._fit = FitResult("degraded", "形态外推的滚动误差样本不足，未伪造区间。")
            return self._fit
        self._fit = FitResult(
            "success",
            f"已对齐到「{top['name']}」。{ANALOG_DISCLAIMER}",
            {
                "n_obs": observed,
                "analog_id": top["analog_id"],
                "combined_score": top["combined_score"],
                "phase": top["phase"],
                "direction": top["direction"],
            },
        )
        return self._fit

    def _point(self, history: np.ndarray, horizon: int) -> float:
        matched = match_values(
            history,
            context=self._context,
            peers=self._peers,
            horizons=self._horizons,
        )
        top = matched.get("top") or {}
        continuation = top.get("continuation") or []
        if not continuation:
            last = last_finite(history)
            if last is None:
                raise ValueError("没有可见值")
            return last[1]
        index = min(len(continuation) - 1, max(0, int(horizon) - 1))
        return float(continuation[index])

    def predict(
        self,
        horizons: list[int],
        available_covariates: dict | None = None,
        *,
        target_week_ends: dict[int, pd.Timestamp] | None = None,
    ) -> PredictResult:
        if self._fit is None or self._fit.status != "success" or self._values is None:
            message = self._fit.message if self._fit else "尚未拟合"
            status = self._fit.status if self._fit is not None else "failed"
            return PredictResult(self.model_id, status, message, metadata=self.serialize_metadata())
        note = covariate_note(available_covariates) + " " + ANALOG_DISCLAIMER
        top = (self._match or {}).get("top") or {}
        last_week = last_visible_week_end(self._index, self._values) if self._index is not None else None
        output: dict[int, HorizonForecast] = {}
        for horizon in horizons:
            steps = int(horizon)
            if target_week_ends is not None:
                if last_week is None or int(horizon) not in target_week_ends:
                    return PredictResult(self.model_id, "failed", "无法把形态类比对齐到统一目标周。", metadata=self.serialize_metadata())
                steps = weeks_between(last_week, target_week_ends[int(horizon)])
                if steps < 1:
                    return PredictResult(self.model_id, "failed", "目标周不晚于最后可见周，拒绝改贴日期。", metadata=self.serialize_metadata())
            point = self._point(self._values, int(steps))
            quantiles, method = self._quantiles_for(point, int(horizon))
            if quantiles is None:
                return PredictResult(self.model_id, "degraded", "无法为全部预测时长构建形态外推区间。", metadata=self.serialize_metadata())
            quality = note + f" 对齐 {top.get('name')}，阶段 {top.get('phase')}，方向 {top.get('direction')}。"
            output[int(horizon)] = HorizonForecast(int(horizon), quantiles, method, quality)
        return PredictResult(self.model_id, "success", self._fit.message, output, self.serialize_metadata())

    def _quantiles_for(self, point: float, horizon: int):
        errors = self._errors.get(horizon, np.array([]))
        method = "analog_residual_quantiles"
        if errors.size < self._min_residuals:
            base = self._errors.get(1, np.array([]))
            if base.size < self._min_residuals:
                return None, method
            errors = base * np.sqrt(horizon)
            method = "analog_one_step_residuals_scaled_by_sqrt_h"
        quantiles = empirical_predictive_quantiles(point, errors, QUANTILE_LEVELS, self._min_residuals)
        return quantiles, method

    def diagnostics(self) -> dict[str, Any]:
        top = (self._match or {}).get("top")
        return {
            "model_id": self.model_id,
            "status": None if self._fit is None else self._fit.status,
            "match": top,
            "disclaimer": ANALOG_DISCLAIMER,
        }

    def serialize_metadata(self) -> dict[str, Any]:
        top = (self._match or {}).get("top") or {}
        return {
            **self.diagnostics(),
            "analog_id": top.get("analog_id"),
            "similarity": top.get("combined_score"),
            "shape_score": top.get("shape_score"),
            "context_score": top.get("context_score"),
            "phase": top.get("phase"),
            "direction": top.get("direction"),
            "blend_weight": analog_blend_weight(top.get("combined_score")),
            "assumptions": "形态类比外推：相对变化对齐历史定性曲线或对照地区，再乘以当前可见水平。",
            "interval_method": "analog_residual_quantiles",
            "keeps_no_transfer_baseline": True,
        }
