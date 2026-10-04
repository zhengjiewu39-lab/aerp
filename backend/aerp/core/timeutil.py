from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_timestamp(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("UTC").tz_localize(None)
    return stamp


def as_date(value) -> date:
    return as_timestamp(value).date()


def isoformat(value) -> str:
    stamp = as_timestamp(value)
    if stamp == stamp.normalize():
        return stamp.date().isoformat()
    return stamp.isoformat(sep=" ", timespec="seconds")
