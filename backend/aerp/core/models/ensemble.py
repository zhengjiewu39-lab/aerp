from __future__ import annotations

import numpy as np
import pandas as pd

from aerp.core.models.base import HorizonForecast, PredictResult
from aerp.core.quantiles import ordered_nonnegative
from aerp.core.timeutil import as_timestamp


def dynamic_weights(
    records: list[dict],
    model_ids: list[str],
    cutoff,
    eps: float = 1e-6,
) -> tuple[dict[str, float], str]:
    """只使用 matured_at 不晚于 cutoff、且已有 WIS 的记录。"""
    if not model_ids:
        return {}, "没有候选模型"
    cutoff_ts = as_timestamp(cutoff)
    usable: list[dict] = []
    for record in records:
        matured = record.get("matured_at")
        if matured is None or record.get("wis") is None:
            continue
        if as_timestamp(matured) <= cutoff_ts and record.get("model_id") in model_ids:
            usable.append(record)
    if not usable:
        weight = 1.0 / len(model_ids)
        return {model_id: weight for model_id in model_ids}, "尚无已成熟预测，动态权重退回等权"

    grouped = {model_id: [] for model_id in model_ids}
    for record in usable:
        grouped[record["model_id"]].append(float(record["wis"]))
    known = {model_id: float(np.mean(scores)) for model_id, scores in grouped.items() if scores}
    inverse = {model_id: 1.0 / (score + eps) for model_id, score in known.items()}
    prior = float(np.mean(list(inverse.values())))
    raw = {model_id: inverse.get(model_id, prior) for model_id in model_ids}
    total = sum(raw.values())
    weights = {model_id: value / total for model_id, value in raw.items()}
    return weights, "权重由截止时间之前已成熟预测的平均 WIS 倒数计算；未成熟结果未使用。动态组合不预设优于等权。"


def apply_downweight(weights: dict[str, float], model_ids: list[str], factor: float = 0.5) -> dict[str, float]:
    raw = dict(weights)
    for model_id in model_ids:
        if model_id in raw:
            raw[model_id] *= factor
    total = sum(raw.values())
    if total <= 0:
        share = 1.0 / max(len(raw), 1)
        return {model_id: share for model_id in raw}
    return {model_id: value / total for model_id, value in raw.items()}


def combine_predictions(
    results: list[PredictResult],
    weights: dict[str, float],
    *,
    model_id: str,
    note: str,
) -> PredictResult:
    usable = [result for result in results if result.status == "success" and weights.get(result.model_id, 0) > 0 and result.horizons]
    excluded = [result.model_id for result in results if result not in usable]
    if not usable:
        return PredictResult(model_id, "failed", "组件均未成功，组合未产生预测。", metadata={"excluded": excluded, "note": note})
    raw_weights = {result.model_id: float(weights.get(result.model_id, 0.0)) for result in usable}
    total = sum(raw_weights.values())
    normalized = {key: value / total for key, value in raw_weights.items()}
    horizon_sets = [set(result.horizons) for result in usable]
    shared = sorted(set.intersection(*horizon_sets))
    if not shared:
        return PredictResult(model_id, "failed", "成功组件没有共同的预测时长。", metadata={"excluded": excluded})
    horizons: dict[int, HorizonForecast] = {}
    for horizon in shared:
        levels = sorted(usable[0].horizons[horizon].quantiles)
        combined: dict[float, float] = {level: 0.0 for level in levels}
        methods = []
        for result in usable:
            forecast = result.horizons[horizon]
            methods.append(forecast.method)
            for level in levels:
                combined[level] += normalized[result.model_id] * float(forecast.quantiles[level])
        quality = note
        horizons[horizon] = HorizonForecast(horizon, ordered_nonnegative(combined), "weighted_quantile_average", quality)
    return PredictResult(
        model_id,
        "success",
        "已完成分位数加权平均。",
        horizons,
        {
            "weights": normalized,
            "excluded": excluded,
            "combination": "对各成功模型的对应分位数做加权平均，不是完整混合分布。",
            "component_interval_methods": sorted(set(methods)),
            "note": note,
        },
    )


def equal_weight_map(model_ids: list[str]) -> dict[str, float]:
    if not model_ids:
        return {}
    share = 1.0 / len(model_ids)
    return {model_id: share for model_id in model_ids}


def matured_before(records: list[dict], cutoff) -> list[dict]:
    cutoff_ts = as_timestamp(cutoff)
    kept = []
    for record in records:
        matured = record.get("matured_at")
        if matured is not None and as_timestamp(matured) <= cutoff_ts:
            kept.append(record)
    return kept


def empty_weight_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["model_id", "wis", "matured_at"])
