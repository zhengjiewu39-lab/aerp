from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from aerp.core.calendar import last_visible_week_end, weeks_between
from aerp.core.models.base import FitResult, HorizonForecast, PredictResult, check_units, covariate_note
from aerp.core.quantiles import QUANTILE_LEVELS, ordered_nonnegative
from aerp.core.series import finite_count, prepare_weekly


def _poisson_ppf(level: float, mu: float) -> float:
    """纯 NumPy 泊松分位数，避免在部分 macOS 上加载损坏的 SciPy 二进制。"""
    mu = float(np.clip(mu, 1e-9, 1e6))
    level = float(np.clip(level, 1e-9, 1 - 1e-9))
    if mu > 80:
        # 正态近似 + 连续性修正
        z = _normsinv(level)
        return max(0.0, mu + z * math.sqrt(mu) - 0.5)
    # 小均值：累积 PMF
    target = level
    cdf = 0.0
    term = math.exp(-mu)
    cdf += term
    if cdf >= target:
        return 0.0
    k = 0
    while k < int(mu + 50 * math.sqrt(mu) + 40):
        k += 1
        term *= mu / k
        cdf += term
        if cdf >= target:
            return float(k)
    return float(k)


def _nbinom_ppf(level: float, mean: float, alpha: float) -> float:
    """负二项（均值-离散参数化）分位数。"""
    mean = float(np.clip(mean, 1e-9, 1e6))
    alpha = float(max(alpha, 1e-9))
    n = 1.0 / alpha
    p = n / (n + mean)
    # 用 Gamma-Poisson 混合的正态近似，失败时退回泊松
    variance = mean + alpha * mean * mean
    if variance <= 0:
        return _poisson_ppf(level, mean)
    z = _normsinv(level)
    approx = mean + z * math.sqrt(variance)
    return max(0.0, approx)


def _normsinv(p: float) -> float:
    """Abramowitz & Stegun 26.2.23 近似。"""
    p = float(np.clip(p, 1e-12, 1 - 1e-12))
    if p < 0.5:
        return -_normsinv(1 - p)
    t = math.sqrt(-2.0 * math.log(1 - p))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return t - (c0 + c1 * t + c2 * t * t) / (1 + d1 * t + d2 * t * t + d3 * t * t * t)


def predictive_quantiles(mean: float, alpha: float, levels: tuple[float, ...] = QUANTILE_LEVELS) -> tuple[dict[float, float], str]:
    mu = float(np.clip(mean, 1e-6, 1e6))
    if not np.isfinite(alpha) or alpha < 1e-6:
        raw = {level: _poisson_ppf(level, mu) for level in levels}
        return ordered_nonnegative(raw), "poisson_predictive_distribution"
    raw = {level: _nbinom_ppf(level, mu, alpha) for level in levels}
    return ordered_nonnegative(raw), "negative_binomial_predictive_distribution"


def _design(time_index: np.ndarray, dates: pd.DatetimeIndex, scale: float) -> np.ndarray:
    weeks = np.array([int(pd.Timestamp(item).isocalendar().week) % 52 for item in dates], dtype=float)
    trend = time_index.astype(float) / scale
    return np.column_stack(
        [
            np.ones(len(time_index)),
            trend,
            np.sin(2 * np.pi * weeks / 52.0),
            np.cos(2 * np.pi * weeks / 52.0),
        ]
    )


def _fit_poisson_irls(
    design: np.ndarray, y: np.ndarray, max_iter: int = 80, *, offset: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """泊松对数链接 IRLS。offset 加在线性预测上。不依赖 statsmodels / sklearn。"""
    if design.ndim != 2 or design.shape[0] != len(y):
        raise RuntimeError("泊松 IRLS 设计矩阵形状不匹配")
    keep = [0] if design.shape[1] else []
    for column in range(1, design.shape[1]):
        trial = design[:, keep + [column]]
        if int(np.linalg.matrix_rank(trial, tol=1e-6)) > len(keep):
            keep.append(column)
    dropped = [index for index in range(design.shape[1]) if index not in keep]
    design = design[:, keep]
    rank = int(np.linalg.matrix_rank(design, tol=1e-8))
    if design.shape[1] == 0 or rank == 0:
        raise RuntimeError("设计矩阵秩为 0，无法拟合")
    off = np.zeros(len(y), dtype=float) if offset is None else np.asarray(offset, dtype=float)
    coef = np.zeros(design.shape[1], dtype=float)
    coef[0] = math.log(max(float(np.mean(y)), 1e-3)) - float(np.mean(off))
    converged = False
    iterations = 0
    for iterations in range(1, max_iter + 1):
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            eta_raw = design @ coef + off
        eta = np.clip(np.nan_to_num(eta_raw, nan=0.0, posinf=20.0, neginf=-20.0), -20, 20)
        mu = np.exp(eta)
        z = eta + (y - mu) / np.maximum(mu, 1e-6)
        sw = np.sqrt(np.maximum(mu, 1e-8))
        xw = design * sw[:, None]
        yw = z * sw
        try:
            coef_new, *_ = np.linalg.lstsq(xw, yw, rcond=None)
        except np.linalg.LinAlgError as exc:
            raise RuntimeError(f"泊松 IRLS 失败：{exc}") from exc
        if not np.all(np.isfinite(coef_new)):
            raise RuntimeError("泊松 IRLS 产生非有限系数")
        coef_new = np.clip(coef_new, -12, 12)
        mixed = np.clip(coef + 0.5 * (coef_new - coef), -12, 12)
        delta = float(np.max(np.abs(mixed - coef)))
        coef = mixed
        if delta < 1e-5:
            converged = True
            break
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        eta_final = design @ coef + off
    mu = np.exp(np.clip(np.nan_to_num(eta_final, nan=0.0, posinf=20.0, neginf=-20.0), -20, 20))
    info = {
        "converged": converged,
        "iterations": iterations,
        "rank": rank,
        "n_params": int(design.shape[1]),
        "dropped_columns": dropped,
        "kept_columns": keep,
    }
    if not converged:
        info["message"] = f"泊松 IRLS 在 {max_iter} 次迭代后未达到 1e-6 容差"
    return np.clip(mu, 1e-6, 1e6), coef, info


class NegBinGLMModel:
    """泊松 GLM 估计均值，按残差判断使用泊松或负二项预测分布。"""

    model_id = "retrain_glm"

    def __init__(self, model_id: str = "retrain_glm") -> None:
        self.model_id = model_id
        self._fit: FitResult | None = None
        self._coef: np.ndarray | None = None
        self._alpha = 0.0
        self._scale = 1.0
        self._index: pd.DatetimeIndex | None = None
        self._engine = ""
        self._method = ""
        self._origin_date: pd.Timestamp | None = None
        self._last_week: pd.Timestamp | None = None
        self._fit_info: dict[str, Any] = {}
        self._keep: list[int] | None = None

    def fit(self, training_data: pd.DataFrame, config: dict | None = None) -> FitResult:
        config = config or {}
        unit_error = check_units(training_data)
        if unit_error:
            self._fit = FitResult("failed", unit_error)
            return self._fit
        index, values = prepare_weekly(training_data)
        self._index = index
        finite = np.where(np.isfinite(values))[0]
        observed = finite_count(values)
        minimum = int(config.get("min_obs", 24))
        if observed == 0:
            self._fit = FitResult("degraded", "全部缺失。缺失值没有替换为 0，也没有伪造训练成功。")
            return self._fit
        if observed < minimum:
            self._fit = FitResult("degraded", f"有效样本 {observed} 周，低于 {minimum} 周，未伪造训练成功。")
            return self._fit
        y = values[finite]
        if np.any(y < 0):
            self._fit = FitResult("failed", "计数模型遇到负值，未把负值改成 0 后假装拟合成功。")
            return self._fit
        dates = pd.DatetimeIndex(index[finite])
        self._origin_date = pd.Timestamp(index[0])
        self._last_week = last_visible_week_end(index, values)
        time_index = np.array([(pd.Timestamp(item) - self._origin_date).days / 7.0 for item in dates], dtype=float)
        self._scale = float(max(float(np.max(time_index)), 1.0))
        design = _design(time_index, dates, self._scale)
        try:
            mu, coef, info = _fit_poisson_irls(design, y)
        except Exception as exc:  # noqa: BLE001
            self._fit = FitResult("failed", f"计数回归未能收敛：{exc}")
            return self._fit
        self._coef = coef
        self._engine = "numpy_poisson_irls"
        self._fit_info = info
        self._keep = list(info.get("kept_columns") or range(len(coef)))
        alpha = self._moment_alpha(y, mu)
        self._alpha = alpha
        self._method = "negative_binomial_predictive_distribution" if alpha >= 1e-6 else "poisson_predictive_distribution"
        metadata = {
            "n_obs": observed,
            "alpha": alpha,
            "engine": self._engine,
            "interval_method": self._method,
            **info,
        }
        if not info.get("converged"):
            self._fit = FitResult("degraded", info.get("message") or "计数回归未收敛。", metadata)
            return self._fit
        self._fit = FitResult(
            "success",
            "已拟合计数回归。该模型不识别传播参数。",
            metadata,
        )
        return self._fit

    @staticmethod
    def _moment_alpha(y: np.ndarray, mu: np.ndarray) -> float:
        denominator = float(np.sum(mu**2))
        if denominator <= 0:
            return 0.0
        numerator = float(np.sum((y - mu) ** 2 - mu))
        return max(0.0, numerator / denominator)

    def predict(
        self,
        horizons: list[int],
        available_covariates: dict | None = None,
        *,
        anchor=None,
        target_week_ends: dict[int, pd.Timestamp] | None = None,
    ) -> PredictResult:
        if self._fit is None or self._fit.status != "success" or self._coef is None or self._index is None:
            message = self._fit.message if self._fit else "尚未拟合"
            status = "degraded" if self._fit and self._fit.status == "degraded" else "failed"
            return PredictResult(self.model_id, status, message)
        note = covariate_note(available_covariates)
        if self._origin_date is None:
            self._origin_date = pd.Timestamp(self._index[0])
        last_week = self._last_week or pd.Timestamp(self._index[-1] if anchor is None else anchor)
        output: dict[int, HorizonForecast] = {}
        for horizon in horizons:
            if target_week_ends is not None:
                if int(horizon) not in target_week_ends:
                    return PredictResult(self.model_id, "failed", "目标周字典缺少对应预测时长。")
                future_date = pd.Timestamp(target_week_ends[int(horizon)])
                if weeks_between(last_week, future_date) < 1:
                    return PredictResult(self.model_id, "failed", "目标周不晚于最后可见周，拒绝改贴日期。")
            else:
                anchor_ts = pd.Timestamp(last_week if anchor is None else anchor)
                future_date = anchor_ts + pd.Timedelta(days=7 * int(horizon))
            time_index = np.array([(future_date - self._origin_date).days / 7.0], dtype=float)
            row = _design(time_index, pd.DatetimeIndex([future_date]), self._scale)[0]
            if self._keep is not None and row.size > max(self._keep):
                row = row[self._keep]
            mean = float(np.exp(np.clip(np.dot(row, self._coef), -20, 20)))
            quantiles, method = predictive_quantiles(mean, self._alpha)
            output[int(horizon)] = HorizonForecast(int(horizon), quantiles, method, note)
        metadata = self.serialize_metadata()
        metadata["origin_date"] = None if self._origin_date is None else self._origin_date.date().isoformat()
        return PredictResult(self.model_id, "success", self._fit.message, output, metadata)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "status": None if self._fit is None else self._fit.status,
            "alpha": self._alpha,
            "engine": self._engine,
        }

    def serialize_metadata(self) -> dict[str, Any]:
        return {
            **self.diagnostics(),
            "assumptions": "对数均值含时间项和年内周期。预测区间来自泊松或负二项分布，不代表已识别传播参数。",
            "interval_method": self._method,
        }
