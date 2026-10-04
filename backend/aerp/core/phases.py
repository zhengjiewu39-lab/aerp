from __future__ import annotations

import numpy as np
import pandas as pd


def classify_phase(series: pd.Series | None, period_end) -> str:
    """事后阶段标签。调用方不得把它传给模型选择。"""
    if series is None or series.empty:
        return "unknown"
    ordered = series.sort_index()
    period = pd.Timestamp(period_end)
    if period not in ordered.index:
        return "unknown"
    position = ordered.index.get_loc(period)
    if isinstance(position, slice) or not isinstance(position, int):
        return "unknown"
    if position <= 0 or position >= len(ordered) - 1:
        return "unknown"
    previous, current, following = (float(ordered.iloc[position + offset]) for offset in (-1, 0, 1))
    if not np.isfinite([previous, current, following]).all():
        return "unknown"
    if current >= previous and current >= following and (current > previous or current > following):
        return "peak"
    if current > previous and following >= current:
        return "growth"
    if current < previous and following <= current:
        return "decline"
    return "stable"
