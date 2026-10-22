from __future__ import annotations

import pandas as pd

from aerp.core.calendar import saturday_on_or_before
from aerp.core.timeutil import as_timestamp


def infer_splits(frame: pd.DataFrame, *, metric: str = "hospital_admissions") -> dict:
    """按可用数据完整性划分开发/验证/锁定测试，不硬编码假定某季存在。"""
    work = frame.loc[frame["metric"] == metric].copy() if "metric" in frame.columns else frame.copy()
    if work.empty:
        return {"status": "error", "message": "没有可用于划分的住院序列。", "splits": {}}
    work["period_end"] = pd.to_datetime(work["period_end"])
    start = work["period_end"].min()
    end = work["period_end"].max()
    span_days = int((end - start).days)
    if span_days < 120:
        return {
            "status": "degraded",
            "message": "可见跨度不足 120 天，不能负责任地切出锁定测试集。",
            "splits": {
                "development": {"start": start.date().isoformat(), "end": end.date().isoformat()},
            },
            "span_days": span_days,
        }
    if span_days < 280:
        mid = start + pd.Timedelta(days=span_days * 2 // 3)
        splits = {
            "development": {"start": start.date().isoformat(), "end": saturday_on_or_before(mid).date().isoformat()},
            "validation": {
                "start": (saturday_on_or_before(mid) + pd.Timedelta(days=7)).date().isoformat(),
                "end": end.date().isoformat(),
            },
        }
        return {"status": "success", "message": "数据不足三个完整季节，只划分开发与验证。", "splits": splits, "span_days": span_days}
    first = start + pd.Timedelta(days=span_days // 3)
    second = start + pd.Timedelta(days=2 * span_days // 3)
    splits = {
        "development": {"start": start.date().isoformat(), "end": saturday_on_or_before(first).date().isoformat()},
        "validation": {
            "start": (saturday_on_or_before(first) + pd.Timedelta(days=7)).date().isoformat(),
            "end": saturday_on_or_before(second).date().isoformat(),
        },
        "locked_test": {
            "start": (saturday_on_or_before(second) + pd.Timedelta(days=7)).date().isoformat(),
            "end": end.date().isoformat(),
        },
    }
    return {
        "status": "success",
        "message": "已按可见跨度三分开发、验证和锁定测试。锁定测试期间不得按测试成绩改阈值。",
        "splits": splits,
        "span_days": span_days,
        "data_start": as_timestamp(start).date().isoformat(),
        "data_end": as_timestamp(end).date().isoformat(),
    }
