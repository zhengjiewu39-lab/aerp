"""可退出的跨疾病迁移原型。只在可比呼吸道住院目标上研究，不拼接原始计数。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from aerp.core.models.base import HorizonForecast, PredictResult
from aerp.core.quantiles import QUANTILE_LEVELS, mix_predictive_cdfs, ordered_nonnegative
from aerp.core.series import prepare_weekly


def _median_level(frame: pd.DataFrame) -> float:
    if frame is None or frame.empty:
        return 1.0
    if "value" in frame.columns:
        values = pd.to_numeric(frame["value"], errors="coerce").dropna().to_numpy(dtype=float)
    else:
        _index, values = prepare_weekly(frame)
        values = np.asarray(values, dtype=float)
        values = values[np.isfinite(values)]
    if values.size == 0:
        return 1.0
    return float(max(np.median(values), 1e-3))


def _last_window(values: np.ndarray, length: int = 8) -> np.ndarray | None:
    finite = np.asarray(values, dtype=float)
    if finite.size < length:
        return None
    window = finite[-length:]
    if not np.all(np.isfinite(window)):
        return None
    return window


def _zscore(window: np.ndarray) -> np.ndarray:
    std = float(np.std(window))
    if std < 1e-9:
        return np.zeros_like(window)
    return (window - float(np.mean(window))) / std


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom < 1e-12:
        return 0.0
    return float(np.dot(a, b) / denom)


def scale_source_to_target(source_value: float, *, source_scale: float, target_scale: float) -> float:
    return float(source_value) * float(target_scale) / max(float(source_scale), 1e-6)


def retrieve_source_windows(
    source_frames: dict[str, pd.DataFrame],
    query: np.ndarray,
    *,
    source_scales: dict[str, float],
    target_scale: float,
    cutoff,
    window: int = 8,
    horizon: int = 1,
) -> list[dict]:
    """只使用 cutoff 之前的来源疾病窗口。"""
    cutoff_ts = pd.Timestamp(cutoff)
    hits = []
    query_z = _zscore(query)
    for disease_id, frame in source_frames.items():
        work = frame.copy()
        if "period_end" in work.columns:
            work = work.loc[pd.to_datetime(work["period_end"]) <= cutoff_ts]
        index, values = prepare_weekly(work)
        values = np.asarray(values, dtype=float)
        if values.size < window + horizon:
            continue
        src_scale = source_scales.get(disease_id, 1.0)
        for start in range(0, len(values) - window - horizon + 1):
            body = values[start : start + window]
            future = values[start + window : start + window + horizon]
            if not np.all(np.isfinite(body)) or not np.all(np.isfinite(future)):
                continue
            score = _cosine(query_z, _zscore(body))
            if score < 0.35:
                continue
            hits.append(
                {
                    "source_disease_id": disease_id,
                    "score": score,
                    "scaled_future": [
                        scale_source_to_target(float(item), source_scale=src_scale, target_scale=target_scale)
                        for item in future
                    ],
                    "week_end": pd.Timestamp(index[start + window - 1]).date().isoformat() if len(index) else None,
                }
            )
    hits.sort(key=lambda item: item["score"], reverse=True)
    return hits[:12]


def mix_transfer_quantiles(local: dict[float, float], transferred: dict[float, float], weight: float) -> dict[float, float]:
    weight = float(np.clip(weight, 0.0, 1.0))
    if weight <= 0:
        return dict(local)
    if weight >= 1:
        return dict(transferred)
    mixed = mix_predictive_cdfs([local, transferred]) if transferred else dict(local)
    # 用权重在本地与混合之间再插值，避免来源完全覆盖本地
    out = {}
    for level in sorted(set(local) | set(mixed)):
        left = float(local.get(level, mixed.get(level, 0.0)))
        right = float(mixed.get(level, left))
        out[level] = (1.0 - weight) * left + weight * right
    return ordered_nonnegative(out)


def exit_weight(
    history_scores: list[dict],
    *,
    local_id: str = "local",
    transfer_id: str = "transfer_fixed",
    min_n: int = 4,
    default: float = 0.35,
    recent: int = 6,
) -> dict[str, Any]:
    """只用已成熟评分调整迁移权重。样本不足时用预设保守权重。"""
    matured = [
        row
        for row in history_scores
        if row.get("status") in {None, "scored"}
        and row.get("wis") is not None
        and row.get("model_id") in {local_id, transfer_id}
    ]
    by_origin: dict[str, dict[str, list[float]]] = {}
    for row in matured:
        key = str(row.get("cutoff_time") or row.get("origin") or row.get("target_period_end"))
        by_origin.setdefault(key, {}).setdefault(str(row["model_id"]), []).append(float(row["wis"]))
    keys = sorted(by_origin)
    if len(keys) < min_n:
        return {
            "weight": default,
            "exited": False,
            "reason": "mature_scores_insufficient",
            "n_origins": len(keys),
            "trigger": None,
        }
    recent_keys = keys[-recent:]
    local_mean = []
    transfer_mean = []
    for key in recent_keys:
        if local_id in by_origin[key] and transfer_id in by_origin[key]:
            local_mean.append(float(np.mean(by_origin[key][local_id])))
            transfer_mean.append(float(np.mean(by_origin[key][transfer_id])))
    if len(local_mean) < min_n:
        return {
            "weight": default,
            "exited": False,
            "reason": "paired_mature_scores_insufficient",
            "n_origins": len(local_mean),
            "trigger": None,
        }
    local_wis = float(np.mean(local_mean))
    transfer_wis = float(np.mean(transfer_mean))
    if transfer_wis > local_wis * 1.05:
        return {
            "weight": 0.0,
            "exited": True,
            "reason": "negative_transfer",
            "n_origins": len(local_mean),
            "local_wis": local_wis,
            "transfer_wis": transfer_wis,
            "trigger": recent_keys[-1],
        }
    if transfer_wis < local_wis:
        return {
            "weight": min(0.5, default + 0.1),
            "exited": False,
            "reason": "transfer_helping",
            "n_origins": len(local_mean),
            "local_wis": local_wis,
            "transfer_wis": transfer_wis,
            "trigger": None,
        }
    return {
        "weight": default,
        "exited": False,
        "reason": "no_clear_gain",
        "n_origins": len(local_mean),
        "local_wis": local_wis,
        "transfer_wis": transfer_wis,
        "trigger": None,
    }


class TransferModel:
    """来源疾病历史片段检索 + 训练期内尺度变换。不是大型基础模型。"""

    model_id = "transfer"

    def __init__(self) -> None:
        self._fit: dict[str, Any] | None = None

    def fit(
        self,
        target_training_data: pd.DataFrame,
        source_training_data: dict[str, pd.DataFrame] | None = None,
        config: dict | None = None,
    ) -> dict[str, Any]:
        config = dict(config or {})
        target_id = str(config.get("target_disease_id") or "target")
        exclude = bool(config.get("exclude_target_from_training", True))
        cutoff = config.get("cutoff")
        sources = dict(source_training_data or {})
        if exclude:
            sources = {key: value for key, value in sources.items() if str(key) != target_id}
        target_scale = _median_level(target_training_data)
        source_scales = {key: _median_level(frame) for key, frame in sources.items()}
        _index, values = prepare_weekly(target_training_data)
        query = _last_window(np.asarray(values, dtype=float))
        matches = []
        if query is not None and cutoff is not None:
            matches = retrieve_source_windows(
                sources,
                query,
                source_scales=source_scales,
                target_scale=target_scale,
                cutoff=cutoff,
            )
        last = None
        finite = np.asarray(values, dtype=float)
        finite = finite[np.isfinite(finite)]
        if finite.size:
            last = float(finite[-1])
        self._fit = {
            "status": "success" if matches or last is not None else "degraded",
            "message": "已用来源疾病训练可见窗口做尺度变换检索。未使用目标疾病未来数据。",
            "source_disease_id": config.get("source_disease_id"),
            "target_disease_id": target_id,
            "exclude_target_from_training": exclude,
            "target_scale": target_scale,
            "source_scales": source_scales,
            "n_matches": len(matches),
            "matches": matches[:6],
            "last_level": last,
            "related_model": "analog",
            "outputs": None,
        }
        return self._fit

    def predict(self, horizons, available_covariates=None) -> PredictResult:
        del available_covariates
        if not self._fit or self._fit.get("last_level") is None:
            return PredictResult(self.model_id, "failed", "迁移模型尚未拟合或目标序列为空。", {})
        last = float(self._fit["last_level"])
        matches = self._fit.get("matches") or []
        horizons_out = {}
        for horizon in horizons:
            horizon = int(horizon)
            if matches:
                scaled = []
                for item in matches:
                    future = item.get("scaled_future") or []
                    if len(future) >= 1:
                        scaled.append(float(future[min(horizon, len(future)) - 1]))
                point = float(np.median(scaled)) if scaled else last
                spread = float(np.std(scaled)) if len(scaled) >= 2 else max(last * 0.25, 1.0)
            else:
                point = last
                spread = max(last * 0.35, 1.0)
            quantiles = ordered_nonnegative(
                {
                    level: max(0.0, point + spread * {0.025: -2.0, 0.1: -1.28, 0.25: -0.67, 0.5: 0.0, 0.75: 0.67, 0.9: 1.28, 0.975: 2.0}[level])
                    for level in QUANTILE_LEVELS
                }
            )
            horizons_out[horizon] = HorizonForecast(
                horizon,
                quantiles,
                "source_window_retrieval_scaled",
                "来源窗口按训练期中位数尺度变换后检索。不是原始计数混训。",
            )
        return PredictResult(self.model_id, "success", self._fit["message"], horizons_out, dict(self._fit))

    def diagnostics(self) -> dict[str, Any]:
        if not self._fit:
            return {"status": "not_fitted", "message": "尚未拟合。"}
        return {
            "status": self._fit.get("status"),
            "message": self._fit.get("message"),
            "n_matches": self._fit.get("n_matches"),
            "exclude_target_from_training": self._fit.get("exclude_target_from_training"),
            "related_model": "analog",
        }

    def serialize_metadata(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "status": None if not self._fit else self._fit.get("status"),
            "message": "呼吸道住院目标上的可解释片段检索迁移。保留不迁移与固定迁移对照，并允许根据已成熟评分退出。",
            "related_model": "analog",
            "keeps_no_transfer_baseline": True,
            "outputs": None if not self._fit else {"n_matches": self._fit.get("n_matches")},
        }


def blend_local_and_transfer(
    local: PredictResult,
    transferred: PredictResult,
    *,
    weight: float,
    model_id: str,
    note: str,
) -> PredictResult:
    if local.status != "success":
        return PredictResult(model_id, local.status, local.message, {}, {"weight": weight})
    if transferred.status != "success" or weight <= 0:
        clone = PredictResult(model_id, local.status, note, dict(local.horizons), {"weight": 0.0, "used_transfer": False})
        return clone
    horizons = {}
    for horizon, item in local.horizons.items():
        other = transferred.horizons.get(horizon)
        quantiles = item.quantiles if other is None else mix_transfer_quantiles(item.quantiles, other.quantiles, weight)
        horizons[horizon] = HorizonForecast(int(horizon), quantiles, f"{item.method}|transfer_mix", note)
    return PredictResult(model_id, "success", note, horizons, {"weight": weight, "used_transfer": True})
