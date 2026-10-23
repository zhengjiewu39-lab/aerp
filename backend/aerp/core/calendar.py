from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from aerp.core.timeutil import as_timestamp

CALENDAR_DEFINITIONS = {
    "cutoff": "发布截止时间：只使用 issued_at ≤ cutoff 的监测版本。",
    "data_week": "数据对应周：观测的 period_start 至 period_end。本系统默认流行病学周为周日到周六。",
    "reference_week": "预测参考周：由参考周规则从 cutoff 推出的周六结束周。所有地区共用，不取各区最后可见周。",
    "target_week": "目标周：reference_week_end + horizon × 7 天，即目标周六。",
    "horizon": "预测时长：目标周相对参考周的整周数。模型外推步数由最后可见周到目标周计算，不能把延迟观测错误贴到另一目标日期。",
}


def saturday_on_or_before(value) -> pd.Timestamp:
    stamp = as_timestamp(value).normalize()
    weekday = int(stamp.weekday())  # Monday=0 ... Saturday=5, Sunday=6
    delta = (weekday - 5) % 7
    return stamp - pd.Timedelta(days=int(delta))


def sunday_of_week(saturday) -> pd.Timestamp:
    return as_timestamp(saturday).normalize() - pd.Timedelta(days=6)


def weeks_between(start, end) -> int:
    start_ts = as_timestamp(start).normalize()
    end_ts = as_timestamp(end).normalize()
    days = int((end_ts - start_ts).days)
    if days % 7 != 0:
        return int(round(days / 7.0))
    return days // 7


def last_visible_week_end(index: pd.DatetimeIndex, values) -> pd.Timestamp | None:
    if index is None or len(index) == 0:
        return None
    import numpy as np

    finite = np.where(np.isfinite(np.asarray(values, dtype=float)))[0]
    if finite.size == 0:
        return None
    return pd.Timestamp(index[int(finite[-1])])


@dataclass(frozen=True)
class ForecastTask:
    cutoff: pd.Timestamp
    reference_week_end: pd.Timestamp
    horizons: tuple[int, ...]
    rule: str = "saturday_on_or_before_cutoff"
    disease_id: str | None = None
    region_id: str | None = None
    target: str | None = None

    @property
    def reference_week_start(self) -> pd.Timestamp:
        return sunday_of_week(self.reference_week_end)

    def target_week_end(self, horizon: int) -> pd.Timestamp:
        return self.reference_week_end + pd.Timedelta(days=7 * int(horizon))

    def target_week_start(self, horizon: int) -> pd.Timestamp:
        return sunday_of_week(self.target_week_end(horizon))

    def target_week_ends(self) -> dict[int, pd.Timestamp]:
        return {int(horizon): self.target_week_end(int(horizon)) for horizon in self.horizons}

    def steps_from(self, last_visible, horizon: int) -> int:
        if last_visible is None:
            raise ValueError("没有最后可见周，无法计算预测步数")
        target = self.target_week_end(horizon)
        steps = weeks_between(last_visible, target)
        if steps < 1:
            raise ValueError(
                f"目标周 {target.date()} 不晚于最后可见周 {as_timestamp(last_visible).date()}，拒绝改贴日期。"
            )
        return int(steps)

    def as_dict(self, last_visible=None) -> dict:
        payload = {
            "cutoff": self.cutoff.isoformat(sep=" ", timespec="seconds"),
            "reference_week_end": self.reference_week_end.date().isoformat(),
            "reference_week_start": self.reference_week_start.date().isoformat(),
            "horizons": list(self.horizons),
            "target_week_ends": {
                str(horizon): stamp.date().isoformat() for horizon, stamp in self.target_week_ends().items()
            },
            "rule": self.rule,
            "disease_id": self.disease_id,
            "region_id": self.region_id,
            "target": self.target,
            "definitions": dict(CALENDAR_DEFINITIONS),
        }
        if last_visible is not None:
            payload["last_visible_week_end"] = as_timestamp(last_visible).date().isoformat()
            payload["steps"] = {
                str(horizon): self.steps_from(last_visible, horizon) for horizon in self.horizons
            }
        return payload


def build_forecast_task(
    cutoff,
    horizons: list[int],
    *,
    rule: str = "saturday_on_or_before_cutoff",
    week_grid=None,
    disease_id: str | None = None,
    region_id: str | None = None,
    target: str | None = None,
) -> ForecastTask:
    cutoff_ts = as_timestamp(cutoff)
    if week_grid is not None:
        grid = pd.DatetimeIndex(pd.to_datetime(list(week_grid))).unique().sort_values()
        eligible = grid[grid <= cutoff_ts]
        if len(eligible) == 0:
            raise ValueError("cutoff 之前没有数据周，无法定义参考周")
        reference = pd.Timestamp(eligible[-1])
        if rule == "saturday_before_cutoff" and reference.normalize() >= cutoff_ts.normalize() and len(eligible) >= 2:
            reference = pd.Timestamp(eligible[-2])
        return ForecastTask(
            cutoff=cutoff_ts,
            reference_week_end=reference,
            horizons=tuple(int(item) for item in horizons),
            rule=f"{rule}|data_grid",
            disease_id=disease_id,
            region_id=region_id,
            target=target,
        )
    if rule == "saturday_on_or_before_cutoff":
        reference = saturday_on_or_before(cutoff_ts)
    elif rule == "saturday_before_cutoff":
        candidate = saturday_on_or_before(cutoff_ts)
        if candidate.normalize() >= cutoff_ts.normalize():
            reference = candidate - pd.Timedelta(days=7)
        else:
            reference = candidate
    else:
        raise ValueError(f"未知参考周规则: {rule}")
    return ForecastTask(
        cutoff=cutoff_ts,
        reference_week_end=reference,
        horizons=tuple(int(item) for item in horizons),
        rule=rule,
        disease_id=disease_id,
        region_id=region_id,
        target=target,
    )
