from __future__ import annotations

import numpy as np
import pandas as pd


def prepare_weekly(frame: pd.DataFrame, index: pd.DatetimeIndex | None = None) -> tuple[pd.DatetimeIndex, np.ndarray]:
    """整理单指标周序列。缺失保持为 NaN，不用 0 填补。

    若提供统一日历 index，则按该坐标对齐，不把各地区自己的序列长度当作共同时间。
    """
    if frame is None or frame.empty:
        empty = index if index is not None else pd.DatetimeIndex([])
        return empty, np.full(len(empty), np.nan, dtype=float)
    work = frame.copy()
    work["period_end"] = pd.to_datetime(work["period_end"])
    work["value"] = pd.to_numeric(work["value"], errors="coerce")
    work = work.sort_values("period_end").drop_duplicates("period_end", keep="last")
    if index is None:
        start = work["period_end"].min()
        end = work["period_end"].max()
        index = pd.date_range(start, end, freq="7D")
    series = work.set_index("period_end")["value"].reindex(index)
    return pd.DatetimeIndex(index), series.to_numpy(dtype=float)


def shared_weekly_index(frames: list[pd.DataFrame]) -> pd.DatetimeIndex:
    ends = []
    for frame in frames:
        if frame is None or frame.empty or "period_end" not in frame.columns:
            continue
        ends.append(pd.to_datetime(frame["period_end"]))
    if not ends:
        return pd.DatetimeIndex([])
    combined = pd.concat(ends, ignore_index=True).dropna()
    if combined.empty:
        return pd.DatetimeIndex([])
    return pd.date_range(combined.min(), combined.max(), freq="7D")


def finite_count(values: np.ndarray) -> int:
    if values.size == 0:
        return 0
    return int(np.isfinite(values).sum())


def last_finite(values: np.ndarray) -> tuple[int, float] | None:
    if values.size == 0:
        return None
    indexes = np.where(np.isfinite(values))[0]
    if indexes.size == 0:
        return None
    position = int(indexes[-1])
    return position, float(values[position])


def is_weekly(period_ends: pd.Series) -> bool:
    unique = pd.to_datetime(pd.Series(period_ends).dropna().unique())
    unique = unique.sort_values()
    if len(unique) <= 1:
        return True
    days = np.diff(unique.to_numpy(dtype="datetime64[D]")).astype(int)
    return bool(np.all(days == 7))


def frame_from_grid(index: pd.DatetimeIndex, values: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame({"period_end": index, "period_start": index - pd.Timedelta(days=6), "value": values})


def tail_weeks(frame: pd.DataFrame, weeks: int) -> pd.DataFrame:
    work = frame.copy()
    work["period_end"] = pd.to_datetime(work["period_end"])
    last = work["period_end"].max()
    start = last - pd.Timedelta(days=7 * (weeks - 1))
    return work.loc[work["period_end"] >= start].copy()
