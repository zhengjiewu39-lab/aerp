"""Map values, coverage, and classification. No fabricated country fills."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from aerp.core.diseases import get_disease
from aerp.core.hashing import canonical_hash
from aerp.core.timeutil import as_timestamp, isoformat, utcnow
from aerp.core.vintages import latest_visible
from aerp.db.models import Forecast, GeoUnit, MapClassification, PopulationEstimate
from aerp.services.geo_catalog import name_candidates, resolve_geo_id, reviewed_mapping_index
from aerp.core.map_demo import SOURCE_ID as MAP_DEMO_SOURCE
from aerp.services.observations import observations_frame

# Sequential heatmap (yellow → orange → red). Not an official clinical severity scale.
RELATIVE_COLORS = ["#fff3b0", "#ffcc4d", "#fb8c00", "#e53935", "#b71c1c", "#7f0000"]
MISSING_COLOR = "#c5cbd3"
ZERO_COLOR = "#f7f8fa"
STALE_OUTLINE = "#b45309"
FORECAST_OUTLINE = "#6d28d9"
COUNT_WARNING = "绝对人数受人口规模影响，不能直接当作负担或严重程度。"
BURDEN_LABELS = ["轻微", "一般", "较重", "严重", "非常严重"]
RELATIVE_NOTE = "五档名称用于读热力图，表示本图内相对负担，不是临床诊断，也不是卫生部门的官方疫情等级。"
ABSOLUTE_NOTE = "每10万展示档 map_burden_v1，跨周同一颜色表示同一数值区间。这是本系统的研究展示档，不是 WHO 或各国官方等级。"
ABSOLUTE_RATE_BREAKS = [0.0, 1.0, 5.0, 15.0, 40.0, 1_000_000.0]
TREND_LABELS = {"up": "档位上升", "down": "档位下降", "stable": "档位未变", "unknown": "无法比较"}

METRIC_LABELS = {
    "weekly_cases": {"name": "周新增报告病例", "unit": "例", "kind": "count"},
    "weekly_deaths": {"name": "周报告死亡数", "unit": "例", "kind": "count"},
    "hospital_admissions": {"name": "周新增住院", "unit": "例", "kind": "count"},
    "rate_per_100k": {"name": "每10万人周报告数", "unit": " /10万", "kind": "rate"},
    "wow_change": {"name": "相对上一可比周变化", "unit": "%", "kind": "trend"},
}


def _status_for_value(value, *, quality: str | None, lag_days: int | None, stale_after: int = 21) -> str:
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        if quality in {"missing", "unreported"}:
            return "unreported"
        return "no_data"
    if float(value) == 0:
        base = "zero"
    else:
        base = "reported"
    if quality in {"partial", "delayed"}:
        return "stale" if (lag_days or 0) > stale_after else "partial"
    if lag_days is not None and lag_days > stale_after:
        return "stale"
    return base


def _finite(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def population_for(db: Session, geo_id: str) -> dict[str, Any] | None:
    row = db.scalars(
        select(PopulationEstimate).where(PopulationEstimate.geo_id == geo_id).order_by(PopulationEstimate.year.desc())
    ).first()
    if row is None:
        return None
    return {"geo_id": geo_id, "year": row.year, "population": row.population, "source": row.source, "version": row.version}


def _map_source_region(
    db: Session,
    region_id: str,
    source_id: str | None,
    mapping_index: dict | None = None,
    *,
    include_candidates: bool = False,
) -> dict[str, Any]:
    geo_id, method = resolve_geo_id(db, region_id, source_id=source_id, mapping_index=mapping_index)
    if geo_id:
        return {"source_region_id": region_id, "geo_id": geo_id, "match_method": method, "review_status": "reviewed"}
    payload = {
        "source_region_id": region_id,
        "geo_id": None,
        "match_method": "unmatched",
        "review_status": "unmatched",
    }
    if include_candidates:
        payload["candidates"] = name_candidates(db, region_id)
    return payload


def available_metrics(db: Session, disease_id: str) -> list[str]:
    frame = observations_frame(db, disease_id=disease_id)
    if frame.empty:
        spec = get_disease(disease_id)
        return list(spec.targets) if spec else []
    return sorted(set(frame["metric"].astype(str)))


def map_periods(db: Session, disease_id: str, metric: str, frame: Any | None = None) -> list[dict[str, Any]]:
    if frame is None:
        frame = observations_frame(db, disease_id=disease_id, metric=metric)
    if frame.empty:
        return []
    visible = latest_visible(frame, utcnow())
    if visible.empty:
        return []
    periods = (
        visible[["period_start", "period_end"]]
        .drop_duplicates()
        .sort_values("period_end")
    )
    return [
        {"period_start": isoformat(row.period_start), "period_end": isoformat(row.period_end)}
        for row in periods.itertuples(index=False)
    ]


def _week_snapshot(frame: pd.DataFrame, period_end, cutoff) -> pd.DataFrame:
    visible = latest_visible(frame, cutoff)
    if visible.empty:
        return visible
    target = as_timestamp(period_end).normalize()
    return visible.loc[pd.to_datetime(visible["period_end"]).dt.normalize() == target].copy()


def _latest_per_region(frame: pd.DataFrame, cutoff) -> pd.DataFrame:
    visible = latest_visible(frame, cutoff)
    if visible.empty:
        return visible
    visible = visible.sort_values("period_end")
    return visible.groupby("region_id", as_index=False, dropna=False).tail(1)


def _burden_labels(n: int) -> list[str]:
    if n >= 5:
        return list(BURDEN_LABELS)
    if n <= 0:
        return []
    if n == 1:
        return ["一般"]
    if n == 2:
        return ["轻微", "非常严重"]
    if n == 3:
        return ["轻微", "一般", "非常严重"]
    return ["轻微", "一般", "严重", "非常严重"]


def build_absolute_rate_classification() -> dict[str, Any]:
    return {
        "method": "absolute_rate_research",
        "breaks": list(ABSOLUTE_RATE_BREAKS),
        "labels": list(BURDEN_LABELS),
        "colors": RELATIVE_COLORS[:5],
        "is_official_severity": False,
        "note": ABSOLUTE_NOTE,
        "version": "map_burden_v1",
        "fixed": True,
    }


def build_classification(values: list[float], *, method: str = "quantile_relative") -> dict[str, Any]:
    if method == "absolute_rate_research":
        return build_absolute_rate_classification()
    finite = sorted(v for v in values if v is not None and v > 0)
    if method != "quantile_relative" or len(finite) < 4:
        if not finite:
            breaks = [0.0]
        else:
            lo, hi = min(finite), max(finite)
            if lo == hi:
                breaks = [0.0, hi]
            else:
                step = (hi - lo) / 5
                breaks = [0.0] + [lo + step * i for i in range(1, 6)]
        labels = _burden_labels(max(1, len(breaks) - 1))
        return {
            "method": "equal_interval_relative" if finite else "empty",
            "breaks": breaks,
            "labels": labels,
            "colors": RELATIVE_COLORS[: len(labels)],
            "is_official_severity": False,
            "note": RELATIVE_NOTE if finite else "本周没有正值，无法分组。",
        }
    quantiles = [0.2, 0.4, 0.6, 0.8, 1.0]
    series = pd.Series(finite)
    breaks = [0.0] + [float(series.quantile(q)) for q in quantiles]
    cleaned = [breaks[0]]
    for item in breaks[1:]:
        cleaned.append(max(item, cleaned[-1] + 1e-9))
    return {
        "method": "quantile_relative",
        "breaks": cleaned,
        "labels": list(BURDEN_LABELS),
        "colors": RELATIVE_COLORS[:5],
        "is_official_severity": False,
        "note": RELATIVE_NOTE,
    }


def _bin_value(value: float | None, classification: dict[str, Any], data_status: str) -> dict[str, Any]:
    if data_status in {"no_data", "unreported"}:
        return {"bin": None, "color": MISSING_COLOR, "label": "无数据" if data_status == "no_data" else "未报告"}
    if data_status == "zero" or (value is not None and value == 0):
        return {"bin": 0, "color": ZERO_COLOR, "label": "报告为零"}
    breaks = classification.get("breaks") or []
    labels = classification.get("labels") or []
    colors = classification.get("colors") or RELATIVE_COLORS
    index = 0
    for i in range(1, len(breaks)):
        if value is not None and value <= breaks[i]:
            index = i - 1
            break
        index = min(len(labels) - 1, max(0, len(breaks) - 2))
    return {"bin": index + 1, "color": colors[min(index, len(colors) - 1)], "label": labels[min(index, len(labels) - 1)]}


def _trend_dir(current_bin: int | None, previous_bin: int | None) -> str:
    if current_bin is None or previous_bin is None:
        return "unknown"
    if current_bin > previous_bin:
        return "up"
    if current_bin < previous_bin:
        return "down"
    return "stable"


def _class_histogram(features: list[dict[str, Any]], classification: dict[str, Any]) -> list[dict[str, Any]]:
    labels = classification.get("labels") or []
    colors = classification.get("colors") or RELATIVE_COLORS
    counts = {label: 0 for label in labels}
    zero_n = 0
    missing_n = 0
    for feat in features:
        status = feat.get("data_status")
        if status in {"no_data", "unreported"}:
            missing_n += 1
        elif feat.get("bin") == 0 or status == "zero":
            zero_n += 1
        else:
            label = feat.get("label")
            if label in counts:
                counts[label] += 1
            else:
                missing_n += 1
    bins = [{"label": label, "n": counts[label], "color": colors[i] if i < len(colors) else RELATIVE_COLORS[-1]} for i, label in enumerate(labels)]
    bins.append({"label": "报告为零", "n": zero_n, "color": ZERO_COLOR})
    bins.append({"label": "无数据", "n": missing_n, "color": MISSING_COLOR})
    return bins


def _layer_value(row_value: float | None, population: dict | None, prev: float | None, metric_view: str) -> dict[str, Any]:
    count = _finite(row_value)
    rate = None
    rate_status = None
    if population and population.get("population"):
        pop = int(population["population"])
        if pop > 0 and count is not None:
            rate = count / pop * 100_000
        elif pop <= 0:
            rate_status = "zero_denominator"
    else:
        rate_status = "no_denominator"
    change = None
    change_status = None
    if prev is None:
        change_status = "no_baseline"
    elif prev == 0 and (count or 0) == 0:
        change = 0.0
    elif prev == 0:
        change_status = "undefined_growth"
    elif count is None:
        change_status = "no_current"
    else:
        change = (count - prev) / prev * 100
    if metric_view == "rate_per_100k":
        return {"value": rate, "unit": "/10万", "display_metric": "rate_per_100k", "rate_status": rate_status, "change": change, "change_status": change_status, "count": count}
    if metric_view == "wow_change":
        return {"value": change, "unit": "%", "display_metric": "wow_change", "rate_status": rate_status, "change": change, "change_status": change_status, "count": count}
    return {"value": count, "unit": "例", "display_metric": "count", "rate_status": rate_status, "change": change, "change_status": change_status, "count": count}


def _select_map_frame(db: Session, frame, *, parent_geo_id: str | None) -> tuple[Any, bool]:
    """Prefer real country-mapped series on the world layer; otherwise use labeled map demo."""
    if frame is None or getattr(frame, "empty", True):
        return frame, False
    demo_mask = frame["source_id"].astype(str) == MAP_DEMO_SOURCE
    real = frame.loc[~demo_mask].copy()
    demo = frame.loc[demo_mask].copy()
    if parent_geo_id:
        return real, False
    if real.empty:
        return demo, not demo.empty
    mapping_index = reviewed_mapping_index(db)
    country_ids: set[str] = set()
    for item in real[["region_id", "source_id"]].drop_duplicates().itertuples(index=False):
        geo_id, _method = resolve_geo_id(db, str(item.region_id), source_id=str(item.source_id), mapping_index=mapping_index)
        if geo_id and str(geo_id).startswith("iso3166-1:"):
            country_ids.add(str(geo_id))
            if len(country_ids) >= 8:
                return real, False
    if not demo.empty:
        return demo, True
    return real, False


def map_observations(
    db: Session,
    *,
    disease_id: str,
    metric: str,
    period_end: str | None,
    time_mode: str = "unified",
    view: str = "rate_per_100k",
    data_mode: str = "real",
    parent_geo_id: str | None = None,
    cutoff: datetime | None = None,
    replay_cutoff: datetime | None = None,
    lock_period_end: str | None = None,
    class_mode: str = "quantile",
) -> dict[str, Any]:
    spec = get_disease(disease_id)
    cutoff_ts = replay_cutoff or utcnow()
    requested_metric = metric
    raw_frame = observations_frame(db, disease_id=disease_id, metric=metric)
    if (raw_frame is None or raw_frame.empty) and metric != "weekly_cases":
        alt = observations_frame(db, disease_id=disease_id, metric="weekly_cases")
        if alt is not None and not alt.empty:
            metric = "weekly_cases"
            raw_frame = alt
    frame, using_demo = _select_map_frame(db, raw_frame, parent_geo_id=parent_geo_id)
    demo = bool(spec and spec.is_synthetic) or data_mode == "demo" or using_demo
    periods = map_periods(db, disease_id, metric, frame)
    visible_all = latest_visible(frame, cutoff_ts) if not frame.empty else frame
    last_reported_period = None
    if visible_all is not None and not visible_all.empty:
        flagged = visible_all.loc[visible_all["value"].notna() & ~visible_all["quality_flag"].isin(["unreported", "missing"])]
        if not flagged.empty:
            work = flagged.copy()
            work["_end"] = pd.to_datetime(work["period_end"]).dt.normalize()
            work["_value"] = pd.to_numeric(work["value"], errors="coerce")
            positive = work.loc[work["_value"] > 0]
            if not positive.empty:
                grouped = positive.groupby("_end")
                n_pos = grouped.size()
                usable = n_pos[n_pos >= 4]
                if using_demo:
                    totals = grouped["_value"].sum()
                    pick_index = usable.index if not usable.empty else n_pos.index
                    last_reported_period = isoformat(totals.loc[pick_index].idxmax())
                else:
                    last_reported_period = isoformat(usable.index.max() if not usable.empty else n_pos.index.max())
            else:
                counts = work.groupby("_end").size()
                usable = counts[counts >= 8]
                last_reported_period = isoformat(usable.index.max() if not usable.empty else work["_end"].max())
    requested_period_end = period_end
    period_clamped = False
    if not period_end:
        period_end = last_reported_period or (periods[-1]["period_end"] if periods else None)
    elif periods:
        wanted = str(period_end)[:10]
        matched = next((item["period_end"] for item in periods if str(item["period_end"])[:10] == wanted), None)
        if matched:
            period_end = matched
        else:
            period_end = last_reported_period or periods[-1]["period_end"]
            period_clamped = True
    if frame.empty or not period_end:
        units = db.scalars(select(GeoUnit).where(GeoUnit.level == ("adm1" if parent_geo_id else "country"))).all()
        if parent_geo_id:
            units = [row for row in units if row.parent_geo_id == parent_geo_id]
        empty_features = [
            {
                "geo_id": row.geo_id,
                "name": row.name,
                "data_status": "no_data",
                "value": None,
                "color": MISSING_COLOR,
            }
            for row in units
        ]
        snapshot = {
            "disease_id": disease_id,
            "metric": metric,
            "view": view,
            "period_end": period_end,
            "time_mode": time_mode,
            "n": 0,
        }
        return {
            "disease_id": disease_id,
            "metric": metric,
            "view": view,
            "period_end": period_end,
            "time_mode": time_mode,
            "is_forecast": False,
            "is_demo": demo,
            "watermark": "演示数据，不得当作真实监测" if demo else None,
            "snapshot_id": canonical_hash(snapshot)[:16],
            "snapshot_hash": canonical_hash(snapshot),
            "features": empty_features,
            "unmatched": [],
            "periods": periods,
            "classification": build_classification([]),
            "legend": _legend(metric, view, build_classification([]), sources=[], period_end=period_end, is_forecast=False),
            "coverage": coverage_report(db, disease_id=disease_id, metric=metric, parent_geo_id=parent_geo_id),
            "note": "选定病种/指标在该周期没有观测。全球底图仍显示，无数据不等于 0。",
        }

    if time_mode == "latest":
        week = _latest_per_region(frame, cutoff_ts)
        unified_end = as_timestamp(period_end)
    else:
        week = _week_snapshot(frame, period_end, cutoff_ts)
        unified_end = as_timestamp(period_end)

    # previous comparable week for trend
    prev_end = unified_end - pd.Timedelta(days=7)
    prev_week = _week_snapshot(frame, prev_end, cutoff_ts)
    prev_by_region = {}
    if not prev_week.empty:
        for item in prev_week.itertuples(index=False):
            prev_by_region[str(item.region_id)] = _finite(item.value)

    unmatched = []
    by_geo: dict[str, dict[str, Any]] = {}
    sources = set()
    values_for_class = []
    mapping_index = reviewed_mapping_index(db)
    for item in week.itertuples(index=False):
        mapped = _map_source_region(db, str(item.region_id), str(item.source_id), mapping_index)
        if not mapped["geo_id"]:
            unmatched.append({**mapped, "period_end": isoformat(item.period_end), "value": _finite(item.value), "metric": metric})
            continue
        geo_id = mapped["geo_id"]
        unit = db.get(GeoUnit, geo_id)
        if parent_geo_id and unit and unit.parent_geo_id != parent_geo_id and geo_id != parent_geo_id:
            continue
        if parent_geo_id is None and unit and unit.level != "country":
            # do not paint states onto the world layer, and do not roll them up
            continue
        pop = population_for(db, geo_id)
        lag_days = None
        if time_mode == "latest":
            lag_days = int((unified_end - as_timestamp(item.period_end)).days)
        data_status = _status_for_value(_finite(item.value), quality=item.quality_flag, lag_days=lag_days)
        layer = _layer_value(_finite(item.value), pop, prev_by_region.get(str(item.region_id)), view)
        display_value = layer["value"]
        if view == "wow_change" and layer.get("change_status") == "undefined_growth":
            data_status = "undefined_growth"
            display_value = None
        if view == "rate_per_100k" and display_value is None and layer.get("count") is not None:
            data_status = layer.get("rate_status") or "no_denominator"
        row = {
            "geo_id": geo_id,
            "name": unit.name if unit else geo_id,
            "level": unit.level if unit else None,
            "source_region_id": str(item.region_id),
            "match_method": mapped["match_method"],
            "value": display_value,
            "count": layer.get("count"),
            "rate_per_100k": None if layer.get("rate_status") else (layer["value"] if view == "rate_per_100k" else (layer["count"] / pop["population"] * 100000 if pop and layer.get("count") is not None else None)),
            "change": layer.get("change"),
            "change_status": layer.get("change_status"),
            "unit": layer.get("unit"),
            "raw_metric": metric,
            "period_start": isoformat(item.period_start),
            "period_end": isoformat(item.period_end),
            "issued_at": isoformat(item.issued_at),
            "source_id": item.source_id,
            "quality_flag": item.quality_flag,
            "data_status": data_status,
            "lag_days": lag_days,
            "population": pop,
            "is_forecast": False,
            "prev_value": _layer_value(prev_by_region.get(str(item.region_id)), pop, None, view).get("value") if prev_by_region.get(str(item.region_id)) is not None else None,
        }
        # same geo+period multiple revisions already collapsed by latest_visible
        by_geo[geo_id] = row
        sources.add(item.source_id)
        if view == "rate_per_100k" and isinstance(row["value"], float):
            values_for_class.append(row["value"])
        elif view != "rate_per_100k" and isinstance(row.get("count"), float):
            values_for_class.append(row["count"] if view != "wow_change" else (row["change"] if row["change"] is not None else None))

    values_for_class = [v for v in values_for_class if v is not None]
    stored = db.scalars(
        select(MapClassification).where(MapClassification.disease_id == disease_id, MapClassification.metric == f"{metric}:{view}")
    ).first()
    want_absolute = class_mode == "absolute" and view == "rate_per_100k"
    if stored and stored.method != "quantile_dynamic" and stored.is_official_severity:
        classification = {
            "id": stored.id,
            "method": stored.method,
            "breaks": stored.breaks,
            "labels": stored.labels,
            "colors": stored.colors,
            "is_official_severity": True,
            "note": stored.note or "本图使用已审核的官方分级。",
            "fixed": True,
        }
    elif want_absolute:
        classification = build_absolute_rate_classification()
        classification["class_mode"] = "absolute"
        if view != "rate_per_100k":
            classification["mode_fallback"] = "绝对档只用于每10万人"
    else:
        lock_values = values_for_class
        if lock_period_end and str(lock_period_end) != str(period_end):
            locked_week = _week_snapshot(frame, lock_period_end, cutoff_ts)
            lock_values = []
            for item in locked_week.itertuples(index=False):
                mapped = _map_source_region(db, str(item.region_id), str(item.source_id), mapping_index)
                if not mapped["geo_id"]:
                    continue
                pop = population_for(db, mapped["geo_id"])
                layer = _layer_value(_finite(item.value), pop, None, view)
                if layer.get("value") is not None:
                    lock_values.append(layer["value"])
        classification = build_classification(lock_values)
        classification["fixed"] = True
        classification["class_mode"] = "quantile"
        classification["lock_period_end"] = lock_period_end or period_end
        classification["playback_note"] = "相对档在播放时锁定起始周阈值，避免同一颜色在不同周代表不同数值。"
        if class_mode == "absolute" and view != "rate_per_100k":
            classification["mode_fallback"] = "绝对档只用于每10万人，当前图层改用相对档。"

    wanted_level = "adm1" if parent_geo_id else "country"
    units_q = select(GeoUnit).where(GeoUnit.level == wanted_level)
    units = list(db.scalars(units_q).all())
    if parent_geo_id:
        units = [row for row in units if row.parent_geo_id == parent_geo_id]
        if not units:
            return {
                "disease_id": disease_id,
                "metric": metric,
                "parent_geo_id": parent_geo_id,
                "available": False,
                "message": "没有可靠的下级地区数据或边界，不会把国家值复制到各省州，也不会按人口拆分。",
                "features": [],
                "is_demo": demo,
            }

    features = []
    for unit in units:
        row = by_geo.get(unit.geo_id)
        if row is None:
            features.append(
                {
                    "geo_id": unit.geo_id,
                    "name": unit.name,
                    "level": unit.level,
                    "value": None,
                    "data_status": "no_data",
                    "color": MISSING_COLOR,
                    "label": "无数据",
                    "representative_point": unit.representative_point,
                }
            )
            continue
        bucket = _bin_value(row["value"], classification, row["data_status"])
        if row["data_status"] == "undefined_growth":
            bucket = {"bin": None, "color": "#fde68a", "label": "前期为 0，增长率无定义"}
        if row["data_status"] in {"no_denominator", "zero_denominator"} and view == "rate_per_100k":
            bucket = {"bin": None, "color": MISSING_COLOR, "label": "无可靠人口分母"}
            row["value"] = None
        features.append({**row, **bucket, "representative_point": unit.representative_point})

    for feat in features:
        prev_value = feat.get("prev_value")
        if feat.get("data_status") in {"no_data", "unreported", "undefined_growth", "no_denominator", "zero_denominator"}:
            feat["trend_dir"] = "unknown"
        else:
            prev_status = "no_data" if prev_value is None else ("zero" if prev_value == 0 else "reported")
            prev_bucket = _bin_value(prev_value, classification, prev_status)
            feat["prev_bin"] = prev_bucket.get("bin")
            feat["prev_label"] = prev_bucket.get("label")
            feat["trend_dir"] = _trend_dir(feat.get("bin"), prev_bucket.get("bin"))
        feat["trend_label"] = TREND_LABELS[feat["trend_dir"]]

    snapshot = {
        "disease_id": disease_id,
        "metric": metric,
        "view": view,
        "period_end": str(period_end),
        "time_mode": time_mode,
        "parent_geo_id": parent_geo_id,
        "items": [
            {
                "geo_id": item["geo_id"],
                "value": item.get("value"),
                "count": item.get("count"),
                "period_end": item.get("period_end"),
                "source_id": item.get("source_id"),
                "data_status": item.get("data_status"),
            }
            for item in features
            if item.get("data_status") not in {"no_data"}
        ],
        "unmatched": unmatched,
    }
    snapshot_hash = canonical_hash(snapshot)
    return {
        "disease_id": disease_id,
        "metric": metric,
        "view": view,
        "unit": METRIC_LABELS.get(view, METRIC_LABELS.get(metric, {})).get("unit", ""),
        "period_end": str(period_end),
        "requested_period_end": requested_period_end,
        "period_clamped": period_clamped,
        "requested_metric": requested_metric,
        "time_mode": time_mode,
        "cutoff": isoformat(cutoff_ts),
        "is_forecast": False,
        "is_demo": demo,
        "watermark": "演示数据，不得当作真实监测" if demo else None,
        "snapshot_id": snapshot_hash[:16],
        "snapshot_hash": snapshot_hash,
        "features": features,
        "unmatched": unmatched,
        "unmatched_count": len(unmatched),
        "periods": periods,
        "classification": classification,
        "class_histogram": _class_histogram(features, classification),
        "legend": _legend(metric, view, classification, sources=sorted(sources), period_end=str(period_end), is_forecast=False),
        "coverage": coverage_report(
            db,
            disease_id=disease_id,
            metric=metric,
            parent_geo_id=parent_geo_id,
            unmatched=unmatched,
            mapped_geos=set(by_geo),
        ),
        "count_warning": COUNT_WARNING if view != "rate_per_100k" else None,
        "as_of": isoformat(cutoff_ts),
        "parent_geo_id": parent_geo_id,
        "last_reported_period": last_reported_period,
    }


def _legend(metric: str, view: str, classification: dict[str, Any], *, sources: list[str], period_end: str | None, is_forecast: bool) -> dict[str, Any]:
    meta = METRIC_LABELS.get(view) or METRIC_LABELS.get(metric) or {"name": metric, "unit": ""}
    breaks = classification.get("breaks") or []
    labels = classification.get("labels") or []
    colors = classification.get("colors") or []
    bins = []
    for i, label in enumerate(labels):
        lo = breaks[i] if i < len(breaks) else None
        hi = breaks[i + 1] if i + 1 < len(breaks) else None
        bins.append({"label": label, "min": lo, "max": hi, "color": colors[i] if i < len(colors) else RELATIVE_COLORS[-1]})
    return {
        "metric_name": meta["name"],
        "unit": meta.get("unit"),
        "period": "week",
        "period_end": period_end,
        "method": classification.get("method"),
        "method_note": classification.get("note"),
        "bins": bins,
        "states": [
            {"id": "no_data", "label": "无数据", "color": MISSING_COLOR},
            {"id": "unreported", "label": "未报告", "color": MISSING_COLOR, "outline": "dotted"},
            {"id": "zero", "label": "报告为零", "color": ZERO_COLOR},
            {"id": "stale", "label": "过期或质量不足", "color": None, "outline": STALE_OUTLINE},
            {"id": "forecast", "label": "预测（不是观测）", "color": None, "outline": FORECAST_OUTLINE, "active": is_forecast},
        ],
        "is_forecast": is_forecast,
        "sources": sources,
        "as_of": period_end,
    }


def coverage_report(
    db: Session,
    *,
    disease_id: str,
    metric: str | None = None,
    parent_geo_id: str | None = None,
    unmatched: list | None = None,
    mapped_geos: set[str] | None = None,
) -> dict[str, Any]:
    units = list(db.scalars(select(GeoUnit).where(GeoUnit.level == ("adm1" if parent_geo_id else "country"))).all())
    if parent_geo_id:
        units = [row for row in units if row.parent_geo_id == parent_geo_id]
    unmatched_rows = list(unmatched or [])
    if mapped_geos is None:
        mapped_geos = set()
        frame = observations_frame(db, disease_id=disease_id, metric=metric)
        if not frame.empty:
            latest = latest_visible(frame, utcnow())
            mapping_index = reviewed_mapping_index(db)
            seen = set()
            for item in latest.itertuples(index=False):
                key = str(item.region_id)
                if key in seen:
                    continue
                seen.add(key)
                mapped = _map_source_region(db, key, str(item.source_id), mapping_index)
                if mapped["geo_id"]:
                    mapped_geos.add(mapped["geo_id"])
                elif unmatched is None:
                    unmatched_rows.append(mapped)
    unit_ids = {row.geo_id for row in units}
    with_data = sorted(mapped_geos & unit_ids)
    without = sorted(unit_ids - mapped_geos)
    return {
        "boundary_version": "ne_110m_admin0-2024 + us-states-fips + small-points",
        "n_boundary_units": len(units),
        "n_with_data": len(with_data),
        "n_without_data": len(without),
        "n_unmatched_records": len(unmatched_rows),
        "unmatched_preview": unmatched_rows[:20],
        "with_data": with_data[:80],
        "without_data_preview": without[:40],
        "note": "全球边界完整不代表疾病数据完整。无数据保持灰色，不会填补国家数字。美国州级数据只在下钻层着色。",
        "drilldown": {
            "iso3166-1:US": "有 Natural Earth/TIGER 州界。仅当该病种存在州级观测时着色，不把美国总值复制到各州。",
        },
    }


def region_detail(
    db: Session,
    geo_id: str,
    *,
    disease_id: str,
    metric: str,
    period_end: str | None,
    time_mode: str = "unified",
    view: str = "rate_per_100k",
) -> dict[str, Any]:
    unit = db.get(GeoUnit, geo_id)
    if unit is None:
        return {"error": "unknown_geo", "geo_id": geo_id}
    payload = map_observations(
        db,
        disease_id=disease_id,
        metric=metric,
        period_end=period_end,
        time_mode=time_mode,
        view=view,
        parent_geo_id=unit.parent_geo_id if unit.level == "adm1" else None,
    )
    match = next((item for item in payload.get("features") or [] if item["geo_id"] == geo_id), None)
    frame = observations_frame(db, disease_id=disease_id, metric=metric)
    frame, _using_demo = _select_map_frame(db, frame, parent_geo_id=unit.parent_geo_id if unit.level == "adm1" else None)
    series = []
    if not frame.empty:
        visible = latest_visible(frame, utcnow())
        mapping_index = reviewed_mapping_index(db)
        source_ids = {key[1] for key, gid in mapping_index.items() if gid == geo_id}
        source_ids.add(geo_id)
        visible = visible.loc[visible["region_id"].astype(str).isin(source_ids)].copy()
        for item in visible.itertuples(index=False):
            mapped = _map_source_region(db, str(item.region_id), str(item.source_id), mapping_index)
            if mapped.get("geo_id") != geo_id:
                continue
            series.append(
                {
                    "period_start": isoformat(item.period_start),
                    "period_end": isoformat(item.period_end),
                    "issued_at": isoformat(item.issued_at),
                    "value": _finite(item.value),
                    "quality_flag": item.quality_flag,
                    "source_id": item.source_id,
                }
            )
    series = sorted(series, key=lambda row: row["period_end"])
    forecasts = []
    for row in db.scalars(select(Forecast).where(Forecast.metric == metric).order_by(Forecast.target_period_end.desc()).limit(40)).all():
        mapped, _method = resolve_geo_id(db, row.region_id)
        if mapped != geo_id:
            continue
        forecasts.append(
            {
                "model_id": row.model_id,
                "horizon": row.horizon,
                "target_period_end": isoformat(row.target_period_end),
                "q50": row.q50,
                "q10": row.q10,
                "q90": row.q90,
                "is_forecast": True,
            }
        )
    children = list(db.scalars(select(GeoUnit).where(GeoUnit.parent_geo_id == geo_id)).all())
    return {
        "geo_id": geo_id,
        "name": unit.name,
        "level": unit.level,
        "parent_geo_id": unit.parent_geo_id,
        "representative_point": unit.representative_point,
        "point_note": (unit.extra or {}).get("point_note") if unit.representative_point else None,
        "disputed_note": (unit.extra or {}).get("disputed_note"),
        "current": match,
        "population": population_for(db, geo_id),
        "series": series,
        "forecasts": forecasts[:12],
        "can_drilldown": bool(children) and any(True for _ in children),
        "drilldown_available": bool(children),
        "drilldown_message": None if children else "没有可靠下级边界与数据，不把本区数值复制到下级。",
        "snapshot_id": payload.get("snapshot_id"),
        "snapshot_hash": payload.get("snapshot_hash"),
        "source_url": "https://covid19.who.int/" if (match or {}).get("source_id") == "who_covid19" else None,
        "definition": (get_disease(disease_id).surveillance_definition if get_disease(disease_id) else ""),
    }


def stored_classification(db: Session, disease_id: str, metric: str) -> dict[str, Any]:
    row = db.scalars(
        select(MapClassification).where(MapClassification.disease_id == disease_id, MapClassification.metric == metric)
    ).first()
    if row:
        return {
            "id": row.id,
            "disease_id": row.disease_id,
            "metric": row.metric,
            "method": row.method,
            "breaks": row.breaks,
            "labels": row.labels,
            "colors": row.colors,
            "version": row.version,
            "is_official_severity": row.is_official_severity,
            "note": row.note,
        }
    return build_classification([])
