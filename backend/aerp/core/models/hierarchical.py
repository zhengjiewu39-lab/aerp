from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from aerp.core.models.base import FitResult, HorizonForecast, PredictResult, covariate_note
from aerp.core.models.negbin import _design, predictive_quantiles
from aerp.core.series import last_finite, prepare_weekly

IDENTIFIABILITY_NOTE = (
    "分层计数模型只共享季节与趋势，并对地区截距做经验贝叶斯收缩。"
    "动态更新仅调整地区截距和一条受阻尼的短期斜率，不同时自由更新传播率、报告率和易感比例。"
    "疫苗覆盖率不视为免疫人口比例。地区效应不是已证明的生物学差异。"
    "仅有年龄人口比例、没有分年龄住院时，年龄结构只作地区协变量，不识别年龄组间传播。"
)

COVARIATE_KEYS = ("age_share_65", "household_size", "crowding_index", "log_population")


def _offset(population: float | None) -> float:
    del population
    # 目标是周新增住院计数。人口只作协变量，不用 log(人口) 当偏移，避免把计数均值放大成近乎人均×人口。
    return 0.0


def _covariates(context: dict | None) -> dict[str, float]:
    raw = dict(context or {})
    age = raw.get("age_distribution") or {}
    age65 = age.get("65plus")
    if age65 is None:
        age65 = {"aging": 0.24, "young": 0.10, "balanced": 0.16}.get(raw.get("age_structure"), np.nan)
    household = raw.get("household_size")
    crowding = raw.get("crowding_index")
    population = raw.get("population")
    log_population = np.log(float(population)) if population not in (None, "") and np.isfinite(float(population)) and float(population) > 0 else np.nan
    return {
        "age_share_65": float(age65) if age65 is not None and np.isfinite(float(age65)) else np.nan,
        "household_size": float(household) if household is not None and np.isfinite(float(household)) else np.nan,
        "crowding_index": float(crowding) if crowding is not None and np.isfinite(float(crowding)) else np.nan,
        "log_population": float(log_population),
    }


def _center(values: list[float]) -> tuple[np.ndarray, float]:
    array = np.asarray(values, dtype=float)
    finite = array[np.isfinite(array)]
    mean = float(np.mean(finite)) if finite.size else 0.0
    filled = np.where(np.isfinite(array), array, mean)
    return filled - mean, mean


def _irls(design: np.ndarray, y: np.ndarray, offset: np.ndarray, max_iter: int = 80) -> tuple[np.ndarray, np.ndarray]:
    coef = np.zeros(design.shape[1], dtype=float)
    coef[0] = float(np.log(max(float(np.mean(y)), 1e-3))) - float(np.mean(offset))
    for _ in range(max_iter):
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            eta = np.clip(design @ np.clip(coef, -12, 12) + offset, -20, 20)
        mu = np.exp(eta)
        z = eta + (y - mu) / np.maximum(mu, 1e-6)
        sw = np.sqrt(np.maximum(mu, 1e-8))
        xw = design * sw[:, None]
        yw = z * sw
        coef_new, *_ = np.linalg.lstsq(xw, yw, rcond=None)
        if not np.all(np.isfinite(coef_new)):
            raise RuntimeError("分层泊松 IRLS 产生非有限系数")
        coef_new = np.clip(coef_new, -12, 12)
        if float(np.max(np.abs(coef_new - coef))) < 1e-6:
            coef = coef_new
            break
        coef = coef_new
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        mu = np.exp(np.clip(design @ coef + offset, -20, 20))
    return np.clip(mu, 1e-6, 1e6), coef


def _moment_alpha(y: np.ndarray, mu: np.ndarray) -> float:
    denominator = float(np.sum(mu**2))
    if denominator <= 0:
        return 0.0
    return max(0.0, float(np.sum((y - mu) ** 2 - mu)) / denominator)


def _last_metric(frame: pd.DataFrame | None) -> float | None:
    if frame is None or frame.empty:
        return None
    _index, values = prepare_weekly(frame)
    last = last_finite(values)
    return None if last is None else float(last[1])


class PanelCountModel:
    """跨地区计数分层。pooled=统一系数；hierarchical=部分共享；dynamic=再加短期斜率。"""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self._fit: FitResult | None = None
        self._coef: np.ndarray | None = None
        self._alpha = 0.0
        self._scale = 1.0
        self._intercepts: dict[str, float] = {}
        self._slopes: dict[str, float] = {}
        self._offsets: dict[str, float] = {}
        self._cov_means: dict[str, float] = {}
        self._last_index: dict[str, pd.Timestamp] = {}
        self._last_level: dict[str, float] = {}
        self._holdout: set[str] = set()
        self._mode = "hierarchical"
        self._window_choice = "expanding"
        self._update: dict[str, Any] = {}

    def fit(
        self,
        panels: dict[str, pd.DataFrame],
        *,
        contexts: dict[str, dict] | None = None,
        auxiliaries: dict[str, dict[str, pd.DataFrame]] | None = None,
        config: dict | None = None,
    ) -> FitResult:
        config = config or {}
        contexts = contexts or {}
        auxiliaries = auxiliaries or {}
        self._mode = str(config.get("mode") or self.model_id)
        self._holdout = {str(item) for item in (config.get("holdout_region_ids") or [])}
        rolling = int(config.get("rolling_window_weeks") or 0)
        use_slope = bool(config.get("dynamic_slope", self._mode == "hierarchical_dynamic"))
        train_ids = [region_id for region_id in panels if region_id not in self._holdout]
        rows_y = []
        rows_t = []
        rows_dates = []
        rows_offset = []
        rows_region = []
        rows_cov = {key: [] for key in COVARIATE_KEYS}
        used_features = []
        for region_id in train_ids:
            frame = panels[region_id]
            if rolling:
                work = frame.copy()
                work["period_end"] = pd.to_datetime(work["period_end"])
                last = work["period_end"].max()
                start = last - pd.Timedelta(days=7 * (rolling - 1))
                frame = work.loc[work["period_end"] >= start]
            index, values = prepare_weekly(frame)
            finite = np.where(np.isfinite(values))[0]
            if finite.size < 8:
                continue
            context = contexts.get(region_id) or {}
            cov = _covariates(context)
            mobility = _last_metric((auxiliaries.get(region_id) or {}).get("mobility_index"))
            if mobility is not None:
                cov["mobility_index"] = mobility
            offset = _offset(context.get("population"))
            self._offsets[region_id] = offset
            self._last_index[region_id] = pd.Timestamp(index[int(finite[-1])])
            self._last_level[region_id] = float(values[int(finite[-1])])
            for position in finite:
                rows_y.append(float(values[position]))
                rows_t.append(float(position))
                rows_dates.append(index[position])
                rows_offset.append(offset)
                rows_region.append(region_id)
                for key in COVARIATE_KEYS:
                    rows_cov[key].append(cov[key])
        if len(rows_y) < 40:
            self._fit = FitResult("degraded", f"分层训练有效样本 {len(rows_y)}，低于 40，未伪造训练成功。")
            return self._fit
        y = np.asarray(rows_y, dtype=float)
        if np.any(y < 0):
            self._fit = FitResult("failed", "分层计数模型遇到负值，未改成 0 后继续。")
            return self._fit
        self._scale = float(max(max(rows_t), 1.0))
        calendar = _design(np.asarray(rows_t, dtype=float), pd.DatetimeIndex(rows_dates), self._scale)
        extra = []
        for key in COVARIATE_KEYS:
            centered, mean = _center(rows_cov[key])
            if np.allclose(centered, 0):
                continue
            extra.append(centered)
            self._cov_means[key] = mean
            used_features.append(key)
        design = calendar if not extra else np.column_stack([calendar, *extra])
        offset = np.asarray(rows_offset, dtype=float)
        try:
            mu, coef = _irls(design, y, offset)
        except Exception as exc:  # noqa: BLE001
            self._fit = FitResult("failed", f"分层计数回归未能收敛：{exc}")
            return self._fit
        intercepts = {region_id: 0.0 for region_id in train_ids}
        if self._mode != "pooled_glm":
            with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                residual = np.log(np.maximum(y, 0.5)) - (design @ coef + offset)
            grouped: dict[str, list[float]] = {region_id: [] for region_id in train_ids}
            for region_id, value in zip(rows_region, residual):
                grouped[region_id].append(float(value))
            means = {region_id: float(np.mean(items)) if items else 0.0 for region_id, items in grouped.items()}
            counts = {region_id: len(items) for region_id, items in grouped.items()}
            between = float(np.var(list(means.values()), ddof=1)) if len(means) > 1 else 0.0
            within = float(np.mean([np.var(items, ddof=1) if len(items) > 1 else 1.0 for items in grouped.values()]))
            tau2 = max(between, 1e-6)
            intercepts = {
                region_id: tau2 / (tau2 + within / max(counts[region_id], 1)) * means[region_id]
                for region_id in train_ids
            }
            region_offset = offset + np.asarray([intercepts[item] for item in rows_region], dtype=float)
            mu, coef = _irls(design, y, region_offset)
        self._coef = coef
        self._intercepts = intercepts
        self._alpha = _moment_alpha(y, mu)
        for region_id in train_ids:
            if not use_slope:
                self._slopes[region_id] = 0.0
                continue
            index, values = prepare_weekly(panels[region_id])
            last = last_finite(values)
            if last is None or last[0] < 8:
                self._slopes[region_id] = 0.0
                continue
            diffs = []
            for position in range(max(1, last[0] - 7), last[0] + 1):
                if np.isfinite(values[position]) and np.isfinite(values[position - 1]):
                    diffs.append(float(np.log(values[position] + 0.5) - np.log(values[position - 1] + 0.5)))
            slope = float(np.clip(np.median(diffs) if diffs else 0.0, -0.25, 0.25))
            self._slopes[region_id] = slope
        for region_id, frame in panels.items():
            if region_id in self._last_index:
                continue
            index, values = prepare_weekly(frame)
            last = last_finite(values)
            if last is None:
                continue
            self._last_index[region_id] = pd.Timestamp(index[last[0]])
            self._last_level[region_id] = float(last[1])
            self._offsets[region_id] = _offset((contexts.get(region_id) or {}).get("population"))
            self._intercepts.setdefault(region_id, 0.0)
            self._slopes.setdefault(region_id, 0.0)
        self._window_choice = "rolling" if rolling else "expanding"
        self._update = {
            "mode": self._mode,
            "window": self._window_choice,
            "used_features": used_features,
            "holdout_region_ids": sorted(self._holdout),
            "n_obs": int(len(y)),
            "n_regions": len(train_ids),
            "dynamic_slope": use_slope,
            "identifiability": IDENTIFIABILITY_NOTE,
        }
        self._fit = FitResult("success", "已拟合跨地区计数分层。" + IDENTIFIABILITY_NOTE, dict(self._update))
        return self._fit

    def predict_region(self, region_id: str, horizons: list[int], context: dict | None = None) -> PredictResult:
        if self._fit is None or self._fit.status != "success" or self._coef is None:
            message = self._fit.message if self._fit else "尚未拟合"
            status = self._fit.status if self._fit is not None else "failed"
            return PredictResult(self.model_id, status, message, metadata={"region_id": region_id})
        if region_id not in self._last_index:
            return PredictResult(self.model_id, "degraded", "该地区没有可用于外推的可见住院。", metadata={"region_id": region_id})
        note = covariate_note(None) + " " + IDENTIFIABILITY_NOTE
        if region_id in self._holdout:
            note += " 该地区未进入共享参数估计，只用协变量和全局截距做冷启动。"
        intercept = float(self._intercepts.get(region_id, 0.0))
        slope = float(self._slopes.get(region_id, 0.0))
        offset = float(self._offsets.get(region_id, 0.0))
        anchor = self._last_index[region_id]
        cov = _covariates(context)
        extra = []
        for key in COVARIATE_KEYS:
            if key not in self._cov_means:
                continue
            value = cov.get(key)
            extra.append(0.0 if value is None or not np.isfinite(value) else float(value) - self._cov_means[key])
        output: dict[int, HorizonForecast] = {}
        phi = 0.75
        for horizon in horizons:
            future_date = pd.Timestamp(anchor) + pd.Timedelta(days=7 * int(horizon))
            future_index = np.array([self._scale + int(horizon)], dtype=float)
            calendar = _design(future_index, pd.DatetimeIndex([future_date]), self._scale)[0]
            row = calendar if not extra else np.concatenate([calendar, np.asarray(extra, dtype=float)])
            if row.size < self._coef.size:
                row = np.pad(row, (0, self._coef.size - row.size))
            elif row.size > self._coef.size:
                row = row[: self._coef.size]
            increment = slope * (phi * (1.0 - phi ** int(horizon)) / (1.0 - phi)) if abs(1.0 - phi) > 1e-9 else slope * int(horizon)
            mean = float(np.exp(np.clip(float(np.dot(row, self._coef)) + offset + intercept + increment, -20, 12)))
            last_level = float(self._last_level.get(region_id, mean))
            mean = float(min(mean, max(40.0, 6.0 * max(last_level, 1.0))))
            quantiles, method = predictive_quantiles(mean, self._alpha)
            output[int(horizon)] = HorizonForecast(int(horizon), quantiles, method, note)
        metadata = {
            "region_id": region_id,
            "intercept": intercept,
            "short_slope": slope,
            "holdout": region_id in self._holdout,
            **self.serialize_metadata(),
        }
        return PredictResult(self.model_id, "success", self._fit.message, output, metadata)

    def serialize_metadata(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "status": None if self._fit is None else self._fit.status,
            "interval_method": "poisson_or_negbin_hierarchical",
            "assumptions": IDENTIFIABILITY_NOTE,
            "update": self._update,
            "intercepts": {key: round(value, 4) for key, value in self._intercepts.items()},
            "short_slopes": {key: round(value, 4) for key, value in self._slopes.items()},
        }
