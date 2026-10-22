from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from aerp.core.timeutil import as_timestamp

EVENT_DEFINITION = (
    "住院高负担预警：未来两周（horizon 1 或 2 对应的目标周）中任一周的周住院数超过预设阈值。"
    "默认使用地区计数，不是人口标准化率。没有床位和住院时长数据时，不称为容量超载预测。"
    "连续多个预警周视为同一事件，提前量只计第一次越过阈值。"
)


@dataclass
class AlertTask:
    horizons: tuple[int, ...] = (1, 2)
    rule: str = "any_week"
    scale: str = "counts"
    threshold_source: str = "development_quantile"
    threshold_quantile: float = 0.90


def development_thresholds(frame: pd.DataFrame, *, region_col="region_id", value_col="value", q=0.90) -> dict[str, float]:
    thresholds = {}
    work = frame.copy()
    work[value_col] = pd.to_numeric(work[value_col], errors="coerce")
    for region_id, group in work.groupby(region_col):
        values = group[value_col].dropna().to_numpy(dtype=float)
        if values.size < 12:
            continue
        thresholds[str(region_id)] = float(np.quantile(values, q))
    return thresholds


def _tail_probability(quantiles: dict[float, float], thr: float) -> float:
    levels = sorted(quantiles)
    values = [quantiles[level] for level in levels]
    if thr <= values[0]:
        return 1.0
    if thr >= values[-1]:
        return 0.0
    for left, right, q_left, q_right in zip(values, values[1:], levels, levels[1:]):
        if left <= thr <= right:
            weight = 0.0 if right == left else (thr - left) / (right - left)
            cdf = q_left + weight * (q_right - q_left)
            return float(max(0.0, min(1.0, 1.0 - cdf)))
    return 0.0


def independent_joint_exceed_probability(
    quantiles_h1: dict[float, float], quantiles_h2: dict[float, float], threshold: float
) -> float:
    """独立边际近似。仅作无轨迹时的降级，不能称为连贯联合概率。"""
    p1 = _tail_probability(quantiles_h1, threshold)
    p2 = _tail_probability(quantiles_h2, threshold)
    return 1.0 - (1.0 - p1) * (1.0 - p2)


def joint_exceed_probability(quantiles_h1: dict[float, float], quantiles_h2: dict[float, float], threshold: float) -> float:
    """兼容旧调用：无轨迹时的独立边际近似。新代码应使用 joint_exceed_report。"""
    return independent_joint_exceed_probability(quantiles_h1, quantiles_h2, threshold)


def joint_exceed_from_paths(paths: np.ndarray, horizons: list[int], threshold: float, event_horizons=(1, 2)) -> float:
    if paths is None or getattr(paths, "size", 0) == 0:
        return float("nan")
    index = [horizons.index(int(h)) for h in event_horizons if int(h) in horizons]
    if not index:
        return float("nan")
    hits = np.any(np.asarray(paths, dtype=float)[:, index] > float(threshold), axis=1)
    return float(np.mean(hits))


def joint_exceed_report(
    *,
    quantiles_h1: dict[float, float] | None = None,
    quantiles_h2: dict[float, float] | None = None,
    threshold: float,
    trajectories: dict | None = None,
    event_horizons: tuple[int, ...] = (1, 2),
) -> dict:
    """优先用样本轨迹估计 P(任一周超阈)；没有轨迹才退回独立边际并标记 degraded。"""
    threshold = float(threshold)
    traj_status = (trajectories or {}).get("status")
    paths = None if trajectories is None else trajectories.get("paths")
    horizons = [] if trajectories is None else list(trajectories.get("horizons") or [])
    if traj_status == "success" and paths is not None and getattr(paths, "size", 0) > 0:
        prob = joint_exceed_from_paths(paths, [int(h) for h in horizons], threshold, event_horizons)
        return {
            "probability": prob,
            "method": trajectories.get("method") or "trajectories",
            "degraded": False,
            "n_samples": int(getattr(paths, "shape", [0])[0]),
            "note": trajectories.get("note") or "联合概率来自样本轨迹。",
        }
    if not quantiles_h1 or not quantiles_h2:
        return {
            "probability": float("nan"),
            "method": "unavailable",
            "degraded": True,
            "n_samples": 0,
            "note": "没有轨迹也没有两条边际分位数，不估计联合概率。",
        }
    prob = independent_joint_exceed_probability(quantiles_h1, quantiles_h2, threshold)
    return {
        "probability": prob,
        "method": "independent_marginal",
        "degraded": True,
        "n_samples": 0,
        "note": "无样本轨迹，使用独立边际近似，结果标记为 degraded。",
    }


def brier_score(probs: np.ndarray, outcomes: np.ndarray) -> float:
    if probs.size == 0:
        return float("nan")
    return float(np.mean((probs - outcomes) ** 2))


def reliability_bins(probs: np.ndarray, outcomes: np.ndarray, n_bins: int = 10) -> list[dict]:
    if probs.size == 0:
        return []
    edges = np.linspace(0, 1, n_bins + 1)
    rows = []
    for index in range(n_bins):
        mask = (probs >= edges[index]) & (probs < edges[index + 1] if index < n_bins - 1 else probs <= edges[index + 1])
        if not np.any(mask):
            continue
        rows.append(
            {
                "bin": index,
                "predicted": float(np.mean(probs[mask])),
                "observed": float(np.mean(outcomes[mask])),
                "n": int(mask.sum()),
            }
        )
    return rows


def event_lead_time(flags: list[tuple[pd.Timestamp, bool]], truth: list[tuple[pd.Timestamp, bool]]) -> list[dict]:
    """同一连续事件只计一次提前量。"""
    events = []
    in_event = False
    event_start = None
    for stamp, hit in truth:
        stamp = as_timestamp(stamp)
        if hit and not in_event:
            in_event = True
            event_start = stamp
        if not hit and in_event:
            events.append({"start": event_start, "end": stamp})
            in_event = False
            event_start = None
    if in_event:
        events.append({"start": event_start, "end": None})
    leads = []
    for event in events:
        first_alert = None
        for stamp, alert in flags:
            if as_timestamp(stamp) <= event["start"] and alert:
                first_alert = as_timestamp(stamp)
                break
        if first_alert is None:
            leads.append({"event_start": event["start"], "lead_weeks": None, "detected": False})
        else:
            lead = int(round((event["start"] - first_alert).days / 7.0))
            leads.append({"event_start": event["start"], "lead_weeks": lead, "detected": True})
    return leads


def simulated_loss(false_alarms: int, misses: int, cost_fa: float, cost_miss: float) -> float:
    return float(false_alarms) * float(cost_fa) + float(misses) * float(cost_miss)


def evaluate_joint_alerts(
    forecast_rows: list[dict],
    *,
    thresholds: dict[str, float],
    actuals: list[dict] | None = None,
    sample_tables: dict[tuple, dict] | None = None,
    n_samples: int = 200,
    rho: float = 0.65,
    seed: int = 42,
    event_horizons: tuple[int, ...] = (1, 2),
) -> dict:
    """按 (model, region, cutoff) 估计两周联合超阈概率并对照已实现结果。"""
    from aerp.core.trajectories import sample_copula_trajectories

    grouped: dict[tuple, dict[int, dict]] = {}
    for row in forecast_rows:
        key = (str(row.get("model_id")), str(row.get("region_id")), str(row.get("cutoff_time")))
        grouped.setdefault(key, {})[int(row["horizon"])] = row
    actual_map = {}
    for row in actuals or []:
        actual_map[
            (str(row.get("model_id")), str(row.get("region_id")), str(row.get("target_period_end")), int(row.get("horizon")))
        ] = row.get("actual_value")
    records = []
    for index, (key, by_horizon) in enumerate(sorted(grouped.items())):
        model_id, region_id, cutoff = key
        if region_id not in thresholds:
            continue
        if any(h not in by_horizon for h in event_horizons):
            continue
        threshold = float(thresholds[region_id])
        quantile_by_horizon = {}
        for horizon, row in by_horizon.items():
            quantiles = row.get("quantiles")
            if not quantiles:
                quantiles = {
                    0.025: row.get("q025"),
                    0.1: row.get("q10"),
                    0.25: row.get("q25"),
                    0.5: row.get("q50"),
                    0.75: row.get("q75"),
                    0.9: row.get("q90"),
                    0.975: row.get("q975"),
                }
            if any(value is None for value in quantiles.values()):
                continue
            quantile_by_horizon[int(horizon)] = {float(level): float(value) for level, value in quantiles.items()}
        sample = None if sample_tables is None else sample_tables.get(key)
        if sample is None:
            sample = sample_copula_trajectories(
                quantile_by_horizon,
                n_samples=n_samples,
                rho=rho,
                seed=int(seed) + index,
            )
        report = joint_exceed_report(
            quantiles_h1=quantile_by_horizon.get(event_horizons[0]),
            quantiles_h2=quantile_by_horizon.get(event_horizons[1] if len(event_horizons) > 1 else event_horizons[0]),
            threshold=threshold,
            trajectories=sample,
            event_horizons=event_horizons,
        )
        outcome = None
        hits = []
        for horizon in event_horizons:
            row = by_horizon[horizon]
            target_end = row.get("target_period_end")
            actual = actual_map.get((model_id, region_id, str(target_end), int(horizon)))
            if actual is None:
                actual = row.get("actual_value")
            if actual is None:
                continue
            hits.append(float(actual) > threshold)
        if hits and len(hits) == len(event_horizons):
            outcome = float(any(hits))
        records.append(
            {
                "model_id": model_id,
                "region_id": region_id,
                "cutoff_time": cutoff,
                "threshold": threshold,
                "probability": report["probability"],
                "method": report["method"],
                "degraded": report["degraded"],
                "outcome": outcome,
            }
        )
    scored = [row for row in records if row["outcome"] is not None and np.isfinite(row["probability"])]
    probs = np.array([row["probability"] for row in scored], dtype=float)
    outcomes = np.array([row["outcome"] for row in scored], dtype=float)
    methods = sorted({row["method"] for row in records})
    return {
        "status": "success" if records else "skipped",
        "event_definition": EVENT_DEFINITION,
        "n": len(records),
        "n_scored": len(scored),
        "methods": methods,
        "degraded_share": None if not records else float(np.mean([row["degraded"] for row in records])),
        "brier": None if not scored else brier_score(probs, outcomes),
        "reliability": reliability_bins(probs, outcomes) if scored else [],
        "rows": records[:200],
        "note": "联合概率默认来自高斯 copula 轨迹；仅当轨迹不可用时才使用独立边际并标记 degraded。",
    }
