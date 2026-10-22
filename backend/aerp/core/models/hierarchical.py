from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from aerp.core.calendar import weeks_between
from aerp.core.models.base import FitResult, HorizonForecast, PredictResult, covariate_note
from aerp.core.models.negbin import _design, _fit_poisson_irls, predictive_quantiles
from aerp.core.series import last_finite, prepare_weekly, shared_weekly_index

IDENTIFIABILITY_NOTE = (
    "这是按地区残差均值做经验贝叶斯式收缩的近似分层，不是完整贝叶斯分层模型，也没有 MCMC 后验。"
    "共享季节与趋势后，地区截距按 residual shrinkage 向总体收缩。"
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


def _irls(design: np.ndarray, y: np.ndarray, offset: np.ndarray, max_iter: int = 80) -> tuple[np.ndarray, np.ndarray, dict]:
    return _fit_poisson_irls(design, y, max_iter=max_iter, offset=offset)


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
        self._calendar_start: pd.Timestamp | None = None
        self._fit_info: dict[str, Any] = {}
        self._keep: list[int] | None = None

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
        calendar_frames = []
        prepared: dict[str, tuple[pd.DatetimeIndex, np.ndarray, pd.DataFrame]] = {}
        for region_id in train_ids:
            frame = panels[region_id]
            if rolling:
                work = frame.copy()
                work["period_end"] = pd.to_datetime(work["period_end"])
                last = work["period_end"].max()
                start = last - pd.Timedelta(days=7 * (rolling - 1))
                frame = work.loc[work["period_end"] >= start]
            calendar_frames.append(frame)
            prepared[region_id] = (None, None, frame)  # type: ignore[assignment]
        shared_index = shared_weekly_index(calendar_frames)
        if len(shared_index) == 0:
            self._fit = FitResult("degraded", "分层训练没有可用的统一周日历。")
            return self._fit
        self._calendar_start = pd.Timestamp(shared_index[0])
        rows_y = []
        rows_t = []
        rows_dates = []
        rows_offset = []
        rows_region = []
        rows_cov = {key: [] for key in COVARIATE_KEYS}
        used_features = []
        for region_id in train_ids:
            frame = prepared[region_id][2]
            index, values = prepare_weekly(frame, shared_index)
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
                date = pd.Timestamp(index[position])
                rows_y.append(float(values[position]))
                rows_t.append(float((date - self._calendar_start).days / 7.0))
                rows_dates.append(date)
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
            mu, coef, info = _irls(design, y, offset)
        except Exception as exc:  # noqa: BLE001
            self._fit = FitResult("failed", f"分层计数回归未能收敛：{exc}")
            return self._fit
        keep = list(info.get("kept_columns") or range(design.shape[1]))
        if keep and design.shape[1] != len(coef):
            design = design[:, keep]
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
            mu, coef, info = _irls(design, y, region_offset)
            keep = list(info.get("kept_columns") or range(design.shape[1]))
            if keep and design.shape[1] != len(coef):
                design = design[:, keep]
        self._coef = coef
        self._fit_info = info
        self._keep = list(info.get("kept_columns") or range(len(coef)))
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
            "estimator": "empirical_bayes_residual_shrinkage",
            "calendar_start": None if self._calendar_start is None else self._calendar_start.date().isoformat(),
            **self._fit_info,
        }
        if not self._fit_info.get("converged", True):
            self._fit = FitResult("degraded", self._fit_info.get("message") or "分层 IRLS 未收敛。", dict(self._update))
            return self._fit
        self._fit = FitResult("success", "已拟合跨地区计数分层（残差收缩近似）。" + IDENTIFIABILITY_NOTE, dict(self._update))
        return self._fit

    def predict_region(
        self,
        region_id: str,
        horizons: list[int],
        context: dict | None = None,
        *,
        target_week_ends: dict[int, pd.Timestamp] | None = None,
    ) -> PredictResult:
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
        origin = self._calendar_start or pd.Timestamp(anchor)
        for horizon in horizons:
            if target_week_ends is not None:
                if int(horizon) not in target_week_ends:
                    return PredictResult(self.model_id, "failed", "目标周字典缺少对应预测时长。", metadata={"region_id": region_id})
                future_date = pd.Timestamp(target_week_ends[int(horizon)])
                try:
                    steps = weeks_between(anchor, future_date)
                except Exception as exc:  # noqa: BLE001
                    return PredictResult(self.model_id, "failed", str(exc), metadata={"region_id": region_id})
                if steps < 1:
                    return PredictResult(self.model_id, "failed", "目标周不晚于最后可见周，拒绝改贴日期。", metadata={"region_id": region_id})
            else:
                future_date = pd.Timestamp(anchor) + pd.Timedelta(days=7 * int(horizon))
                steps = int(horizon)
            future_index = np.array([(future_date - origin).days / 7.0], dtype=float)
            calendar = _design(future_index, pd.DatetimeIndex([future_date]), self._scale)[0]
            row = calendar if not extra else np.concatenate([calendar, np.asarray(extra, dtype=float)])
            if self._keep is not None and row.size > max(self._keep):
                row = row[self._keep]
            if row.size < self._coef.size:
                row = np.pad(row, (0, self._coef.size - row.size))
            elif row.size > self._coef.size:
                row = row[: self._coef.size]
            increment = slope * (phi * (1.0 - phi ** int(steps)) / (1.0 - phi)) if abs(1.0 - phi) > 1e-9 else slope * int(steps)
            mean = float(np.exp(np.clip(float(np.dot(row, self._coef)) + offset + intercept + increment, -20, 12)))
            last_level = float(self._last_level.get(region_id, mean))
            cap = 20.0 * max(last_level, 1.0)
            if mean > cap:
                mean = float(cap)
                note += " 均值外推超过最近水平的 20 倍，已截断并记录；这是数值保护，不是已校准区间。"
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
