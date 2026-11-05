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
from aerp.core.geo import WHO_NONGEO_CODES
from aerp.core.map_demo import SOURCE_ID as MAP_DEMO_SOURCE
from aerp.services.observations import observations_frame

# Sequential heatmap (yellow → orange → red). Numeric bins only; not official severity.
RELATIVE_COLORS = ["#fff3b0", "#ffcc4d", "#fb8c00", "#e53935", "#b71c1c", "#7f0000"]
MISSING_COLOR = "#c5cbd3"
UNREPORTED_COLOR = "#d9dee6"
NO_DENOM_COLOR = "#c4b39a"
ZERO_COLOR = "#f7f8fa"
STALE_OUTLINE = "#b45309"
FORECAST_OUTLINE = "#6d28d9"
COUNT_WARNING = "绝对人数受人口规模影响，不能直接当作负担或严重程度。"
CLASS_NOTE = "颜色按本图选定指标的数值区间划分，不是临床诊断，也不是卫生部门官方疫情等级。"
PREFERRED_SOURCES = ("who_flunet", "who_covid19", "flusight", "flusight-nhsn", "nhsn_influenza", "nhsn_covid19", "nhsn_rsv")
US_TERRITORY_COUNTRY_IDS = ("iso3166-1:AS", "iso3166-1:GU", "iso3166-1:MP", "iso3166-1:VI")
TREND_LABELS = {"up": "数值区间上移", "down": "数值区间下移", "stable": "数值区间未变", "unknown": "无法比较"}
POP_YEAR_SLACK = 2

METRIC_LABELS = {
    "weekly_cases": {"name": "周新增报告病例", "unit": "例", "kind": "count"},
    "weekly_deaths": {"name": "周报告死亡数", "unit": "例", "kind": "count"},
    "hospital_admissions": {"name": "周新增住院", "unit": "例", "kind": "count"},
    "lab_detections": {"name": "周实验室检出", "unit": "例", "kind": "count"},
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


def _population_index(db: Session) -> dict[str, PopulationEstimate]:
    index: dict[str, PopulationEstimate] = {}
    for row in db.scalars(select(PopulationEstimate).order_by(PopulationEstimate.year.desc())).all():
        index.setdefault(row.geo_id, row)
    return index


def population_for(
    db: Session,
    geo_id: str,
    period_end: str | datetime | None = None,
    cache: dict[str, PopulationEstimate] | None = None,
) -> dict[str, Any] | None:
    row = cache.get(geo_id) if cache is not None else db.scalars(
        select(PopulationEstimate).where(PopulationEstimate.geo_id == geo_id).order_by(PopulationEstimate.year.desc())
    ).first()
    if row is None:
        return None
    payload = {"geo_id": geo_id, "year": row.year, "population": row.population, "source": row.source, "version": row.version}
    if period_end is not None:
        year = as_timestamp(period_end).year
        if abs(int(row.year) - int(year)) > POP_YEAR_SLACK:
            payload["incompatible"] = True
            payload["reason"] = f"人口年份 {row.year} 与报告年 {year} 相差超过 {POP_YEAR_SLACK} 年，不计算人均率。"
            return payload
    return payload


def _pick_default_period(visible, *, demo: bool) -> str | None:
    """Skip trailing stub weeks. Do not invent values for silent countries."""
    if visible is None or getattr(visible, "empty", True):
        return None
    work = visible.copy()
    work["_end"] = pd.to_datetime(work["period_end"]).dt.normalize()
    work["_value"] = pd.to_numeric(work["value"], errors="coerce")
    flagged = work.loc[work["_value"].notna() & ~work["quality_flag"].isin(["unreported", "missing"])]
    if flagged.empty:
        flagged = work
    n_rep = flagged.drop_duplicates(["_end", "region_id"]).groupby("_end")["region_id"].nunique()
    if n_rep.empty:
        return isoformat(work["_end"].max())
    if demo:
        totals = flagged.groupby("_end")["_value"].sum()
        return isoformat(totals.idxmax())
    peak = int(n_rep.max())
    floor = max(4, int(0.2 * peak)) if peak else 4
    usable = n_rep[n_rep >= floor]
    pick = usable.index.max() if not usable.empty else n_rep.index.max()
    return isoformat(pick)


def _map_source_region(
    db: Session,
    region_id: str,
    source_id: str | None,
    mapping_index: dict | None = None,
    *,
    include_candidates: bool = False,
    units_index: dict | None = None,
) -> dict[str, Any]:
    geo_id, method = resolve_geo_id(
        db, region_id, source_id=source_id, mapping_index=mapping_index, units_index=units_index
    )
    if geo_id:
        return {"source_region_id": region_id, "geo_id": geo_id, "match_method": method, "review_status": "reviewed"}
    payload = {
        "source_region_id": region_id,
        "geo_id": None,
        "match_method": "unmatched",
        "review_status": "unmatched",
        "reason": WHO_NONGEO_CODES.get(str(region_id).upper()),
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
    valued = visible.loc[visible["value"].notna() & ~visible["quality_flag"].isin(["unreported", "missing"])]
    use = valued if not valued.empty else visible
    use = use.sort_values("period_end")
    return use.groupby("region_id", as_index=False, dropna=False).tail(1)


def _range_labels(breaks: list[float], unit: str) -> list[str]:
    labels = []
    for i in range(max(0, len(breaks) - 1)):
        lo, hi = breaks[i], breaks[i + 1]
        labels.append(f"{lo:.3g}–{hi:.3g}{unit}".strip())
    return labels or ["无正值"]


def build_classification(values: list[float], *, method: str = "quantile_relative", unit: str = "") -> dict[str, Any]:
    finite = sorted(v for v in values if v is not None and v > 0)
    suffix = unit or ""
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
        labels = _range_labels(breaks, suffix)
        return {
            "method": "equal_interval" if finite else "empty",
            "breaks": breaks,
            "labels": labels,
            "colors": RELATIVE_COLORS[: len(labels)],
            "is_official_severity": False,
            "note": CLASS_NOTE if finite else "本周没有正值，无法按数值区间着色。",
        }
    quantiles = [0.2, 0.4, 0.6, 0.8, 1.0]
    series = pd.Series(finite)
    breaks = [0.0] + [float(series.quantile(q)) for q in quantiles]
    cleaned = [breaks[0]]
    for item in breaks[1:]:
        cleaned.append(max(item, cleaned[-1] + 1e-9))
    labels = _range_labels(cleaned, suffix)
    return {
        "method": "quantile_locked",
        "breaks": cleaned,
        "labels": labels,
        "colors": RELATIVE_COLORS[:5],
        "is_official_severity": False,
        "note": CLASS_NOTE,
    }


def _bin_value(value: float | None, classification: dict[str, Any], data_status: str) -> dict[str, Any]:
    if data_status == "no_data":
        return {"bin": None, "color": MISSING_COLOR, "label": "无数据"}
    if data_status == "unreported":
        return {"bin": None, "color": UNREPORTED_COLOR, "label": "未报告"}
    if data_status in {"no_denominator", "zero_denominator"}:
        return {"bin": None, "color": NO_DENOM_COLOR, "label": "有报告人数但无适配人口分母"}
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
    usable_pop = population if population and not population.get("incompatible") else None
    if usable_pop and usable_pop.get("population"):
        pop = int(usable_pop["population"])
        if pop > 0 and count is not None:
            rate = count / pop * 100_000
        elif pop <= 0:
            rate_status = "zero_denominator"
    else:
        rate_status = "no_denominator" if not usable_pop else (usable_pop.get("reason") and "incompatible_denominator") or "no_denominator"
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


def _select_map_frame(db: Session, frame, *, parent_geo_id: str | None, data_mode: str = "real") -> tuple[Any, bool]:
    """Real mode never falls back to demo. Demo only when data_mode=demo."""
    if frame is None or getattr(frame, "empty", True):
        return frame, False
    demo_mask = frame["source_id"].astype(str) == MAP_DEMO_SOURCE
    real = frame.loc[~demo_mask].copy()
    demo = frame.loc[demo_mask].copy()
    if str(data_mode) == "demo":
        return demo, not demo.empty
    return real, False


def _source_rank(source_id: str) -> int:
    try:
        return PREFERRED_SOURCES.index(str(source_id))
    except ValueError:
        return len(PREFERRED_SOURCES) + 1


def map_observations(
    db: Session,
    *,
    disease_id: str,
    metric: str,
    period_end: str | None,
    time_mode: str = "unified",
    view: str = "count",
    data_mode: str = "real",
    parent_geo_id: str | None = None,
    cutoff: datetime | None = None,
    replay_cutoff: datetime | None = None,
    lock_period_end: str | None = None,
    class_mode: str = "quantile",
    source_id: str | None = None,
) -> dict[str, Any]:
    spec = get_disease(disease_id)
    cutoff_ts = replay_cutoff or utcnow()
    requested_metric = metric
    raw_frame = observations_frame(db, disease_id=disease_id, metric=metric)
    frame, using_demo = _select_map_frame(db, raw_frame, parent_geo_id=parent_geo_id, data_mode=data_mode)
    demo = data_mode == "demo" or using_demo
    periods = map_periods(db, disease_id, metric, frame)
    visible_all = latest_visible(frame, cutoff_ts) if not frame.empty else frame
    last_reported_period = _pick_default_period(visible_all, demo=using_demo)
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
            "metric_unchanged": requested_metric == metric,
            "switch_metric": None if requested_metric != "hospital_admissions" else ("weekly_cases" if disease_id == "covid19" else None),
            "suggested_metric": (
                "lab_detections"
                if parent_geo_id is None and metric == "hospital_admissions" and spec is not None and "lab_detections" in spec.targets
                else (spec.default_target if spec is not None and metric not in spec.targets else None)
            ),
            "suggested_parent_geo_id": "iso3166-1:US" if requested_metric == "hospital_admissions" and parent_geo_id is None else None,
            "note": (
                "该指标只有美国州级住院，世界图层不会外推着色。"
                if requested_metric == "hospital_admissions" and parent_geo_id is None
                else (
                    "该地区暂无住院数据"
                    if requested_metric == "hospital_admissions"
                    else "选定病种/指标在该周期没有观测。全球底图仍显示，无数据不等于 0。"
                )
            ),
        }

    if time_mode == "latest":
        week = _latest_per_region(frame, cutoff_ts)
        unified_end = as_timestamp(period_end)
    else:
        week = _week_snapshot(frame, period_end, cutoff_ts)
        unified_end = as_timestamp(period_end)

    unmatched = []
    by_geo: dict[str, dict[str, Any]] = {}
    sources = set()
    values_for_class = []
    mapping_index = reviewed_mapping_index(db)
    geo_index = {row.geo_id: row for row in db.scalars(select(GeoUnit)).all()}
    pop_cache = _population_index(db)
    prev_lookup_cache: dict[str, dict[tuple[str, str], float | None]] = {}
    candidates: dict[str, list[dict[str, Any]]] = {}
    skipped_parents: set[str] = set()
    for item in week.itertuples(index=False):
        mapped = _map_source_region(
            db, str(item.region_id), str(item.source_id), mapping_index, units_index=geo_index
        )
        if not mapped["geo_id"]:
            unmatched.append({**mapped, "period_end": isoformat(item.period_end), "value": _finite(item.value), "metric": metric, "source_id": str(item.source_id)})
            continue
        geo_id = mapped["geo_id"]
        unit = geo_index.get(geo_id)
        if parent_geo_id and unit and unit.parent_geo_id != parent_geo_id and geo_id != parent_geo_id:
            if not (parent_geo_id == "iso3166-1:US" and geo_id in US_TERRITORY_COUNTRY_IDS):
                continue
        if parent_geo_id is None and unit and unit.level != "country":
            if unit.parent_geo_id:
                skipped_parents.add(unit.parent_geo_id)
            continue
        if source_id and str(item.source_id) != str(source_id):
            candidates.setdefault(geo_id, []).append({"source_id": str(item.source_id), "value": _finite(item.value), "issued_at": isoformat(item.issued_at)})
            continue
        pop = population_for(db, geo_id, item.period_end, cache=pop_cache)
        lag_days = None
        displayed_end = item.period_end if time_mode == "latest" else unified_end
        if time_mode == "latest":
            lag_days = int((unified_end - as_timestamp(item.period_end)).days)
        prev_target = as_timestamp(displayed_end) - pd.Timedelta(days=7)
        cache_key = str(prev_target.normalize())
        if cache_key not in prev_lookup_cache:
            prev_week = _week_snapshot(frame, prev_target, cutoff_ts)
            lookup: dict[tuple[str, str], float | None] = {}
            if prev_week is not None and not prev_week.empty:
                for prev_item in prev_week.itertuples(index=False):
                    lookup[(str(prev_item.source_id), str(prev_item.region_id))] = _finite(prev_item.value)
            prev_lookup_cache[cache_key] = lookup
        prev_val = prev_lookup_cache[cache_key].get((str(item.source_id), str(item.region_id)))
        data_status = _status_for_value(_finite(item.value), quality=item.quality_flag, lag_days=lag_days)
        layer = _layer_value(_finite(item.value), pop, prev_val, view)
        display_value = layer["value"]
        if view == "wow_change" and layer.get("change_status") == "undefined_growth":
            data_status = "undefined_growth"
            display_value = None
        if view == "rate_per_100k" and display_value is None and layer.get("count") is not None:
            data_status = layer.get("rate_status") or "no_denominator"
        rate_value = None
        if not layer.get("rate_status") and pop and not pop.get("incompatible") and layer.get("count") is not None:
            rate_value = layer["value"] if view == "rate_per_100k" else (layer["count"] / pop["population"] * 100000 if pop.get("population") else None)
        row = {
            "geo_id": geo_id,
            "name": unit.name if unit else geo_id,
            "level": unit.level if unit else None,
            "source_region_id": str(item.region_id),
            "match_method": mapped["match_method"],
            "value": display_value,
            "count": layer.get("count"),
            "rate_per_100k": rate_value,
            "change": layer.get("change"),
            "change_status": layer.get("change_status"),
            "unit": layer.get("unit"),
            "raw_metric": metric,
            "period_start": isoformat(item.period_start),
            "period_end": isoformat(item.period_end),
            "issued_at": isoformat(item.issued_at),
            "source_id": str(item.source_id),
            "quality_flag": item.quality_flag,
            "data_status": data_status,
            "lag_days": lag_days,
            "population": pop,
            "is_forecast": False,
            "prev_value": _layer_value(prev_val, pop, None, view).get("value") if prev_val is not None else None,
            "_rank": _source_rank(str(item.source_id)),
        }
        bucket = candidates.setdefault(geo_id, [])
        bucket.append(row)

    for geo_id, rows in candidates.items():
        display_rows = [item for item in rows if "geo_id" in item and "count" in item]
        if not display_rows:
            continue
        display_rows.sort(key=lambda item: (item.get("_rank", 99), str(item.get("issued_at") or "")))
        chosen = display_rows[0]
        extras = display_rows[1:]
        if extras:
            chosen["source_conflicts"] = [
                {"source_id": item.get("source_id"), "value": item.get("count"), "issued_at": item.get("issued_at"), "period_end": item.get("period_end")}
                for item in extras
            ]
            chosen["comparability"] = {
                "comparable": False,
                "reason": "同一地理单元存在多个不可比来源，未平均、未覆盖。地图显示优先来源，其余列入冲突。",
            }
        else:
            chosen["comparability"] = {"comparable": True, "reason": None}
        chosen.pop("_rank", None)
        by_geo[geo_id] = chosen
        sources.add(chosen["source_id"])
        if view == "rate_per_100k" and isinstance(chosen["value"], float):
            values_for_class.append(chosen["value"])
        elif view != "rate_per_100k" and isinstance(chosen.get("count"), float):
            values_for_class.append(chosen["count"] if view != "wow_change" else (chosen["change"] if chosen["change"] is not None else None))

    values_for_class = [v for v in values_for_class if v is not None]
    stored = db.scalars(
        select(MapClassification).where(MapClassification.disease_id == disease_id, MapClassification.metric == f"{metric}:{view}")
    ).first()
    unit_suffix = METRIC_LABELS.get(view, METRIC_LABELS.get(metric, {})).get("unit", "")
    if stored and stored.is_official_severity:
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
    else:
        lock_values = values_for_class
        if lock_period_end and str(lock_period_end) != str(period_end):
            locked_week = _week_snapshot(frame, lock_period_end, cutoff_ts)
            lock_values = []
            for item in locked_week.itertuples(index=False):
                mapped = _map_source_region(db, str(item.region_id), str(item.source_id), mapping_index)
                if not mapped["geo_id"]:
                    continue
                pop = population_for(db, mapped["geo_id"], item.period_end)
                layer = _layer_value(_finite(item.value), pop, None, view)
                if layer.get("value") is not None:
                    lock_values.append(layer["value"])
        classification = build_classification(lock_values, unit=unit_suffix)
        classification["fixed"] = True
        classification["lock_period_end"] = lock_period_end or period_end
        classification["playback_note"] = "播放时锁定同一指标口径的数值区间，避免同色在不同周代表不同数值。"

    wanted_level = "adm1" if parent_geo_id else "country"
    units_q = select(GeoUnit).where(GeoUnit.level == wanted_level)
    units = list(db.scalars(units_q).all())
    if parent_geo_id:
        units = [row for row in units if row.parent_geo_id == parent_geo_id]
        if parent_geo_id == "iso3166-1:US":
            extra = [geo_index[gid] for gid in US_TERRITORY_COUNTRY_IDS if gid in geo_index]
            units.extend(extra)
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
            bucket = {"bin": None, "color": NO_DENOM_COLOR, "label": "有报告人数但无适配人口分母"}
            row["value"] = None
        comparable = (row.get("comparability") or {}).get("comparable", True) and row.get("data_status") not in {
            "stale",
            "partial",
            "no_denominator",
            "zero_denominator",
            "undefined_growth",
        }
        features.append({**row, **bucket, "representative_point": unit.representative_point, "rank_eligible": comparable})

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
        "n_with_value": sum(1 for item in features if item.get("data_status") in {"reported", "zero", "stale", "partial"}),
        "coverage_note": (
            f"本视图 {sum(1 for item in features if item.get('data_status') in {'reported', 'zero', 'stale', 'partial'})}/{len(features)} "
            "个地区有可核验数值。未报告保持灰色，不是系统漏数。"
        ),
        "rate_unavailable": view == "rate_per_100k" and not any(item.get("rate_per_100k") is not None for item in features),
        "switch_view": "count" if view == "rate_per_100k" and not any(item.get("rate_per_100k") is not None for item in features) else None,
        "suggested_metric": (
            "lab_detections"
            if parent_geo_id is None and metric == "hospital_admissions" and spec is not None and "lab_detections" in spec.targets
            else None
        ),
        "suggested_parent_geo_id": next(iter(skipped_parents))
        if parent_geo_id is None
        and metric == "hospital_admissions"
        and len(skipped_parents) == 1
        and sum(1 for item in features if item.get("count") is not None) <= 1
        else None,
        "note": (
            "人口年份与报告年不匹配，未计算每10万人率。地图按报告人数才有可核验着色。"
            if view == "rate_per_100k" and not any(item.get("rate_per_100k") is not None for item in features)
            else (
                "住院只有美国州级监测，不会涂到其他国家。全球请用周实验室检出（WHO FluNet）。"
                if parent_geo_id is None and metric == "hospital_admissions"
                else (
                    "该指标只有下级行政区数据，世界图层不会外推着色。"
                    if parent_geo_id is None and len(skipped_parents) == 1 and not any(item.get("count") is not None for item in features)
                    else None
                )
            )
        ),
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
            {"id": "unreported", "label": "未报告", "color": UNREPORTED_COLOR, "outline": "dotted"},
            {"id": "zero", "label": "报告为零", "color": ZERO_COLOR},
            {"id": "no_denominator", "label": "有人数、无适配人口分母", "color": NO_DENOM_COLOR},
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
                if str(item.source_id) == MAP_DEMO_SOURCE:
                    continue
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
        "geo_coverage": {"n_boundary_units": len(units), "boundary_version": "ne_110m_admin0-2024 + us-states-fips + small-points"},
        "disease_coverage": {"n_with_data": len(with_data), "n_without_data": len(without), "n_unmatched_records": len(unmatched_rows)},
        "note": "地理覆盖与疾病数据覆盖分开统计。全球边界完整不代表疾病数据完整。无数据保持灰色。美国州级数据只在下钻层着色，不会外推到其他国家。",
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
    view: str = "count",
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
        data_mode="real",
    )
    match = next((item for item in payload.get("features") or [] if item["geo_id"] == geo_id), None)
    frame = observations_frame(db, disease_id=disease_id, metric=metric)
    frame, _using_demo = _select_map_frame(
        db, frame, parent_geo_id=unit.parent_geo_id if unit.level == "adm1" else None, data_mode="real"
    )
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
        "source_url": (
            "https://www.who.int/tools/flunet/"
            if (match or {}).get("source_id") == "who_flunet"
            else ("https://covid19.who.int/" if (match or {}).get("source_id") == "who_covid19" else None)
        ),
        "definition": (get_disease(disease_id).surveillance_definition if get_disease(disease_id) else ""),
        "revision_contrast": revision_contrast(db, geo_id=geo_id, disease_id=disease_id, metric=metric, period_end=period_end, source_id=(match or {}).get("source_id")),
        "comparability": (match or {}).get("comparability") or comparability_check(match, metric=metric, view=view),
    }


def comparability_check(row: dict | None, *, metric: str, view: str) -> dict[str, Any]:
    if not row:
        return {"comparable": False, "reason": "无观测，不能参与排名。"}
    reasons = []
    if row.get("source_conflicts"):
        reasons.append("存在多个来源")
    if view == "rate_per_100k" and row.get("rate_per_100k") is None:
        reasons.append("缺少适配年份的人口分母")
    if row.get("data_status") in {"stale", "partial"}:
        reasons.append(f"报告状态为 {row.get('data_status')}")
    if row.get("quality_flag") in {"partial", "delayed"}:
        reasons.append("部分报告或迟报")
    if reasons:
        return {"comparable": False, "reason": "；".join(reasons)}
    return {"comparable": True, "reason": None, "basis": f"同周、同指标 {metric}、同源 {row.get('source_id')}"}


def revision_contrast(db: Session, *, geo_id: str, disease_id: str, metric: str, period_end: str | None, source_id: str | None) -> dict[str, Any]:
    if not period_end:
        return {"comparable": False, "reason": "未指定报告周。"}
    frame = observations_frame(db, disease_id=disease_id, metric=metric)
    if frame.empty:
        return {"comparable": False, "reason": "没有版本历史。"}
    mapping_index = reviewed_mapping_index(db)
    target = as_timestamp(period_end).normalize()
    work = frame.loc[pd.to_datetime(frame["period_end"]).dt.normalize() == target].copy()
    if source_id:
        work = work.loc[work["source_id"].astype(str) == str(source_id)]
    rows = []
    for item in work.itertuples(index=False):
        mapped = _map_source_region(db, str(item.region_id), str(item.source_id), mapping_index)
        if mapped.get("geo_id") != geo_id:
            continue
        if str(item.source_id) == MAP_DEMO_SOURCE:
            continue
        rows.append(item)
    if len(rows) < 2:
        return {
            "comparable": False,
            "label": "不可比较",
            "reason": "没有可追溯的多次采集/发布版本，不能称为数据订正。",
        }
    rows.sort(key=lambda item: as_timestamp(item.issued_at))
    first, last = rows[0], rows[-1]
    first_val, last_val = _finite(first.value), _finite(last.value)
    delta = None if first_val is None or last_val is None else last_val - first_val
    return {
        "comparable": True,
        "kind": "data_revision",
        "note": "同一报告周、同一来源的首次采集快照与后续修订快照。报告周之间的差异不是数据订正。",
        "source_id": str(last.source_id),
        "period_end": isoformat(last.period_end),
        "first": {"value": first_val, "issued_at": isoformat(first.issued_at), "quality_flag": first.quality_flag},
        "latest": {"value": last_val, "issued_at": isoformat(last.issued_at), "quality_flag": last.quality_flag},
        "delta": delta,
        "crossed_bin": None,
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
