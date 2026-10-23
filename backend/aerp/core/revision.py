from __future__ import annotations

import numpy as np
import pandas as pd

from aerp.core.timeutil import as_timestamp


def estimate_revision(vintages: pd.DataFrame, cutoff, maturity_lag_days: int = 28) -> dict:
    """按报告滞后汇总修订。修订量 = 成熟版本 − 初版，允许为负。"""
    if vintages is None or vintages.empty:
        return {"estimable": False, "message": "无法估计修订过程", "n_pairs": 0, "paths": []}
    work = vintages.copy()
    work["period_end"] = pd.to_datetime(work["period_end"])
    work["issued_at"] = pd.to_datetime(work["issued_at"])
    work["value"] = pd.to_numeric(work["value"], errors="coerce")
    cutoff_ts = as_timestamp(cutoff)
    visible = work.loc[work["issued_at"] <= cutoff_ts]
    deltas: list[float] = []
    relatives: list[float] = []
    by_lag: dict[int, list[float]] = {}
    for period, group in visible.groupby("period_end"):
        known = group.dropna(subset=["value"]).sort_values("issued_at")
        if len(known) < 2:
            continue
        first = known.iloc[0]
        mature_from = pd.Timestamp(period) + pd.Timedelta(days=int(maturity_lag_days))
        mature_rows = known.loc[known["issued_at"] >= mature_from]
        if mature_rows.empty:
            continue
        mature = mature_rows.iloc[0]
        delta = float(mature["value"] - first["value"])
        deltas.append(delta)
        if float(first["value"]) != 0:
            relatives.append(delta / float(first["value"]))
        lag_weeks = int(round((pd.Timestamp(mature["issued_at"]) - pd.Timestamp(period)).days / 7.0))
        by_lag.setdefault(lag_weeks, []).append(delta)
    paths = revision_paths(visible, limit_periods=12)
    if len(deltas) < 8:
        return {
            "estimable": False,
            "message": "无法估计修订过程",
            "n_pairs": len(deltas),
            "paths": paths,
            "note": "固定成熟滞后的值不等于最终真实值。",
        }
    rel = np.asarray(relatives if relatives else [0.0], dtype=float)
    delta_arr = np.asarray(deltas, dtype=float)
    return {
        "estimable": True,
        "message": "已按历史版本估计固定成熟滞后的修订幅度。该数值不等于最终真实值，修订可以向下。",
        "n_pairs": len(deltas),
        "median_delta": float(np.median(delta_arr)),
        "median_relative": float(np.median(rel)),
        "rel_q10": float(np.quantile(rel, 0.1)),
        "rel_q90": float(np.quantile(rel, 0.9)),
        "negative_revision_share": float(np.mean(delta_arr < 0)),
        "by_lag": {
            str(lag): {"n": len(samples), "median_delta": float(np.median(samples))}
            for lag, samples in sorted(by_lag.items())
        },
        "paths": paths,
        "uncertainty_propagated": False,
        "uncertainty_note": "若用修订后的点值参加预测，第一版还没有把修订区间完整传进预测区间。",
    }


def revision_paths(visible: pd.DataFrame, limit_periods: int = 12) -> list[dict]:
    if visible.empty:
        return []
    periods = sorted(visible["period_end"].dropna().unique())[-limit_periods:]
    paths = []
    for period in periods:
        group = visible.loc[visible["period_end"] == period].sort_values("issued_at")
        points = []
        for row in group.itertuples(index=False):
            value = None if pd.isna(row.value) else float(row.value)
            points.append(
                {
                    "issued_at": pd.Timestamp(row.issued_at).isoformat(),
                    "lag_days": int((pd.Timestamp(row.issued_at) - pd.Timestamp(period)).days),
                    "value": value,
                    "quality_flag": getattr(row, "quality_flag", None),
                }
            )
        paths.append({"period_end": pd.Timestamp(period).date().isoformat(), "points": points})
    return paths


def apply_nowcast(frame: pd.DataFrame, cutoff, profile: dict, maturity_lag_days: int = 28, recent_weeks: int = 4) -> pd.DataFrame:
    """用历史相对修订的中位数调整最近的初报。缺失值保持缺失。"""
    out = frame.copy()
    if not profile.get("estimable"):
        return out
    out["period_end"] = pd.to_datetime(out["period_end"])
    out["issued_at"] = pd.to_datetime(out["issued_at"])
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    latest = out["period_end"].max()
    relative = float(profile["median_relative"])
    for index, row in out.iterrows():
        if pd.isna(row["value"]):
            continue
        age_days = int((pd.Timestamp(row["issued_at"]) - pd.Timestamp(row["period_end"])).days)
        weeks_from_end = int(round((pd.Timestamp(latest) - pd.Timestamp(row["period_end"])).days / 7.0))
        if age_days < maturity_lag_days and weeks_from_end < recent_weeks:
            current = float(row["value"])
            if current == 0:
                out.at[index, "value"] = max(0.0, current + float(profile.get("median_delta") or 0.0))
            else:
                out.at[index, "value"] = max(0.0, current * (1.0 + relative))
    return out


def nowcast_interval(value: float, profile: dict) -> dict | None:
    if value is None or (isinstance(value, float) and np.isnan(value)) or not profile.get("estimable"):
        return None
    median = max(0.0, float(value) * (1.0 + float(profile["median_relative"])))
    lower = max(0.0, float(value) * (1.0 + float(profile["rel_q10"])))
    upper = max(0.0, float(value) * (1.0 + float(profile["rel_q90"])))
    lower, median, upper = sorted([lower, median, upper])
    return {
        "median": median,
        "lower": lower,
        "upper": upper,
        "note": "这是固定成熟滞后的估计区间，不等于最终真实值。",
    }


def _revision_pairs(profile: dict) -> tuple[np.ndarray, np.ndarray]:
    deltas = []
    relatives = []
    for path in profile.get("paths") or []:
        points = [point for point in path.get("points") or [] if point.get("value") is not None]
        if len(points) < 2:
            continue
        first = float(points[0]["value"])
        last = float(points[-1]["value"])
        deltas.append(last - first)
        if first != 0:
            relatives.append((last - first) / first)
    return np.asarray(deltas, dtype=float), np.asarray(relatives, dtype=float)


def sample_revised_frames(
    frame: pd.DataFrame,
    cutoff,
    profile: dict,
    *,
    n_samples: int = 20,
    seed: int = 42,
    maturity_lag_days: int = 28,
    recent_weeks: int = 4,
) -> dict:
    """从已成熟修订分布抽样近期未成熟周。初报为 0 时用加法，不用乘法。

    假设：同一地区内各周独立抽样；地区之间独立。这是可复现近似，不是完整时空相关修订过程。
    """
    out_frame = frame.copy()
    if not profile.get("estimable"):
        return {
            "status": "degraded",
            "message": "修订分布不可估计，未进行抽样，保留点修订对照。",
            "samples": [],
            "n_samples": 0,
            "seed": seed,
            "assumptions": "样本不足时透明降级。",
        }
    deltas, relatives = _revision_pairs(profile)
    if deltas.size < 8:
        return {
            "status": "degraded",
            "message": "成熟修订对不足 8 个，未进行抽样。",
            "samples": [],
            "n_samples": 0,
            "seed": seed,
        }
    rng = np.random.default_rng(int(seed))
    work = out_frame.copy()
    work["period_end"] = pd.to_datetime(work["period_end"])
    work["issued_at"] = pd.to_datetime(work["issued_at"])
    work["value"] = pd.to_numeric(work["value"], errors="coerce")
    latest = work["period_end"].max()
    immature = []
    for index, row in work.iterrows():
        if pd.isna(row["value"]):
            continue
        age_days = int((pd.Timestamp(row["issued_at"]) - pd.Timestamp(row["period_end"])).days)
        weeks_from_end = int(round((pd.Timestamp(latest) - pd.Timestamp(row["period_end"])).days / 7.0))
        if age_days < maturity_lag_days and weeks_from_end < recent_weeks:
            immature.append(index)
    samples = []
    for _ in range(int(n_samples)):
        drawn = work.copy()
        for index in immature:
            current = float(drawn.at[index, "value"])
            if current == 0:
                delta = float(rng.choice(deltas))
                drawn.at[index, "value"] = max(0.0, current + delta)
            else:
                rel = float(rng.choice(relatives)) if relatives.size else 0.0
                drawn.at[index, "value"] = max(0.0, current * (1.0 + rel))
        samples.append(drawn)
    return {
        "status": "success",
        "message": "已按已成熟修订对抽样近期未成熟周。正向和负向修订均可出现。",
        "samples": samples,
        "n_samples": len(samples),
        "seed": seed,
        "n_immature": len(immature),
        "assumptions": "周与地区独立抽样；未建模共同修订冲击。覆盖修订不确定性，不覆盖全部参数与未来随机性。",
        "point_revision_control": True,
        "uncertainty_propagated": True,
    }


def mix_quantile_dicts(items: list[dict[float, float]]) -> dict[float, float]:
    """保留的分位数平均组合。调用方必须标明不是完整概率混合。"""
    from aerp.core.quantiles import mix_quantile_average

    return mix_quantile_average(items)


def mix_forecast_distributions(items: list[dict[float, float]]) -> dict[float, float]:
    """等权混合预测 CDF。用于修订抽样后的条件预测合并。"""
    from aerp.core.quantiles import mix_predictive_cdfs

    return mix_predictive_cdfs(items)
