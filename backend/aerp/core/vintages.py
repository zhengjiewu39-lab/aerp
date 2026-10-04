from __future__ import annotations

import pandas as pd

from aerp.core.timeutil import as_timestamp

OBSERVATION_COLUMNS = [
    "disease_id",
    "region_id",
    "metric",
    "period_start",
    "period_end",
    "issued_at",
    "value",
    "unit",
    "source_id",
    "quality_flag",
    "import_batch_id",
]


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for column in ("period_start", "period_end", "issued_at"):
        if column in work.columns:
            work[column] = pd.to_datetime(work[column]).dt.tz_localize(None)
    if "value" in work.columns:
        work["value"] = pd.to_numeric(work["value"], errors="coerce")
    return work


def latest_visible(frame: pd.DataFrame, cutoff, *, require_period_ended: bool = True) -> pd.DataFrame:
    """每个周期保留 cutoff 当时已经发布的最新版本。

    后来的修订不会替代当时可见的输入。缺失值保持为缺失。
    """
    if frame is None or frame.empty:
        return pd.DataFrame(columns=list(frame.columns) if frame is not None else OBSERVATION_COLUMNS)
    work = _normalize(frame)
    cutoff_ts = as_timestamp(cutoff)
    mask = work["issued_at"] <= cutoff_ts
    if require_period_ended:
        mask &= work["period_end"] <= cutoff_ts
    visible = work.loc[mask].sort_values("issued_at")
    if visible.empty:
        return visible
    keys = ["disease_id", "region_id", "metric", "period_start", "source_id"]
    keys = [key for key in keys if key in visible.columns]
    latest = visible.groupby(keys, as_index=False, dropna=False).tail(1)
    leaked_issue = latest["issued_at"].max() > cutoff_ts
    leaked_period = bool(require_period_ended and (latest["period_end"].max() > cutoff_ts))
    if leaked_issue or leaked_period:
        raise RuntimeError("截止时间过滤后仍出现未来发布版本或未来周期")
    return latest.sort_values(["region_id", "metric", "period_start"]).reset_index(drop=True)


def latest_final_by_period(frame: pd.DataFrame, cutoff) -> pd.DataFrame:
    """最终数据时间切分：使用每个周期的最后版本，只保留已经结束的周期。

    该版本的发布时间可以晚于 cutoff。这不是真实实时回测。
    """
    if frame is None or frame.empty:
        return pd.DataFrame(columns=list(frame.columns) if frame is not None else OBSERVATION_COLUMNS)
    work = _normalize(frame).sort_values("issued_at")
    keys = ["disease_id", "region_id", "metric", "period_start", "source_id"]
    keys = [key for key in keys if key in work.columns]
    latest = work.groupby(keys, as_index=False, dropna=False).tail(1)
    cutoff_ts = as_timestamp(cutoff)
    latest = latest.loc[latest["period_end"] <= cutoff_ts].copy()
    if not latest.empty and latest["period_end"].max() > cutoff_ts:
        raise RuntimeError("时间切分过滤后仍出现未来周期")
    return latest.sort_values(["region_id", "metric", "period_start"]).reset_index(drop=True)


def select_snapshot(frame: pd.DataFrame, cutoff, replay_mode: str) -> pd.DataFrame:
    if replay_mode == "vintage":
        return latest_visible(frame, cutoff)
    if replay_mode == "final_time_split":
        return latest_final_by_period(frame, cutoff)
    raise ValueError(f"未知回放模式: {replay_mode}")


def replay_descriptor(replay_mode: str, has_multiple_vintages: bool) -> dict:
    if replay_mode == "vintage" and has_multiple_vintages:
        return {
            "mode": "vintage",
            "label": "真实历史版本回放",
            "is_real_time": True,
            "warning": "每个截止时间只使用当时已经发布的版本。",
        }
    if replay_mode == "vintage":
        return {
            "mode": "vintage",
            "label": "缺少历史发布时间，不能称为真实实时回测",
            "is_real_time": False,
            "warning": "观测没有多个发布时间，版本回放无法重建当时可见的数据。",
        }
    if replay_mode == "final_time_split":
        return {
            "mode": "final_time_split",
            "label": "最终数据时间切分回放",
            "is_real_time": False,
            "warning": "此模式使用各周期的最后版本，修订可能在截止时间之后才发布。不能称为真实实时回测。",
        }
    raise ValueError(f"未知回放模式: {replay_mode}")


def mature_value(
    frame: pd.DataFrame,
    *,
    region_id: str,
    metric: str,
    period_end,
    as_of,
    maturity_lag_days: int,
    replay_mode: str,
    source_id: str | None = None,
):
    """返回固定成熟滞后上的可见目标。没有成熟版本时返回 None，不用初报或 0 代替。"""
    if frame is None or frame.empty:
        return None
    work = _normalize(frame)
    period = as_timestamp(period_end)
    rows = work[(work["region_id"] == region_id) & (work["metric"] == metric) & (work["period_end"] == period)]
    if source_id is not None and "source_id" in rows.columns:
        rows = rows[rows["source_id"] == source_id]
    if rows.empty:
        return None
    as_of_ts = as_timestamp(as_of)
    mature_from = period + pd.Timedelta(days=int(maturity_lag_days))
    if replay_mode == "vintage":
        ready = rows[(rows["issued_at"] <= as_of_ts) & (rows["issued_at"] >= mature_from) & rows["value"].notna()]
        ready = ready.sort_values("issued_at")
        if ready.empty:
            return None
        row = ready.iloc[0]
        return float(row["value"]), pd.Timestamp(row["issued_at"])
    if replay_mode == "final_time_split":
        if mature_from > as_of_ts:
            return None
        ready = rows[rows["value"].notna()].sort_values("issued_at")
        if ready.empty:
            return None
        row = ready.iloc[-1]
        return float(row["value"]), pd.Timestamp(row["issued_at"])
    raise ValueError(f"未知回放模式: {replay_mode}")


def has_multiple_vintages(frame: pd.DataFrame) -> bool:
    if frame is None or frame.empty:
        return False
    keys = ["region_id", "metric", "period_start"]
    counts = frame.groupby(keys)["issued_at"].nunique()
    return bool((counts > 1).any())
