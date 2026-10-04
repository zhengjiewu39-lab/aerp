from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

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


def _fit_poisson_irls(design: np.ndarray, y: np.ndarray, max_iter: int = 80) -> tuple[np.ndarray, np.ndarray]:
    """泊松对数链接 IRLS。不依赖 statsmodels / sklearn。"""
    coef = np.zeros(design.shape[1], dtype=float)
    coef[0] = math.log(max(float(np.mean(y)), 1e-3))
    for _ in range(max_iter):
        eta = design @ coef
        mu = np.exp(np.clip(eta, -20, 20))
        weight = mu
        z = eta + (y - mu) / np.maximum(mu, 1e-6)
        sw = np.sqrt(np.maximum(weight, 1e-8))
        xw = design * sw[:, None]
        yw = z * sw
        try:
            coef_new, *_ = np.linalg.lstsq(xw, yw, rcond=None)
        except np.linalg.LinAlgError as exc:
            raise RuntimeError(f"泊松 IRLS 失败：{exc}") from exc
        if not np.all(np.isfinite(coef_new)):
            raise RuntimeError("泊松 IRLS 产生非有限系数")
        if float(np.max(np.abs(coef_new - coef))) < 1e-6:
            coef = coef_new
            break
        coef = coef_new
    mu = np.exp(np.clip(design @ coef, -20, 20))
    return np.clip(mu, 1e-6, 1e6), coef


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
        self._scale = float(max(finite.max(), 1))
        dates = pd.DatetimeIndex(index[finite])
        design = _design(finite.astype(float), dates, self._scale)
        try:
            mu, coef = _fit_poisson_irls(design, y)
        except Exception as exc:  # noqa: BLE001
            self._fit = FitResult("failed", f"计数回归未能收敛：{exc}")
            return self._fit
        self._coef = coef
        self._engine = "numpy_poisson_irls"
        alpha = self._moment_alpha(y, mu)
        self._alpha = alpha
        self._method = "negative_binomial_predictive_distribution" if alpha >= 1e-6 else "poisson_predictive_distribution"
        self._fit = FitResult(
            "success",
            "已拟合计数回归。该模型不识别传播参数。",
            {"n_obs": observed, "alpha": alpha, "engine": self._engine, "interval_method": self._method},
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
    ) -> PredictResult:
        if self._fit is None or self._fit.status != "success" or self._coef is None or self._index is None:
            message = self._fit.message if self._fit else "尚未拟合"
            status = "degraded" if self._fit and self._fit.status == "degraded" else "failed"
            return PredictResult(self.model_id, status, message)
        note = covariate_note(available_covariates)
        anchor_ts = pd.Timestamp(self._index[-1] if anchor is None else anchor)
        step = int(round((anchor_ts - pd.Timestamp(self._index[-1])).days / 7.0))
        output: dict[int, HorizonForecast] = {}
        for horizon in horizons:
            future_index = (len(self._index) - 1) + step + int(horizon)
            future_date = pd.Timestamp(self._index[-1]) + pd.Timedelta(days=7 * (step + int(horizon)))
            row = _design(np.array([future_index], dtype=float), pd.DatetimeIndex([future_date]), self._scale)[0]
            mean = float(np.exp(np.clip(np.dot(row, self._coef), -20, 20)))
            quantiles, method = predictive_quantiles(mean, self._alpha)
            output[int(horizon)] = HorizonForecast(int(horizon), quantiles, method, note)
        metadata = self.serialize_metadata()
        metadata["anchor_step_weeks"] = step
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
