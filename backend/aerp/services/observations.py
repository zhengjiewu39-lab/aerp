from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aerp.core.analogs import match_values
from aerp.core.diseases import DISEASE_SPECS, get_disease
from aerp.core.recommend import recommend_models
from aerp.core.context import public_context
from aerp.core.disclaimer import EMPTY_BANNER, IMPORTED_BANNER, SYNTHETIC_BANNER
from aerp.core.revision import estimate_revision, nowcast_interval
from aerp.core.series import prepare_weekly
from aerp.core.vintages import has_multiple_vintages, latest_visible, select_snapshot
from aerp.db.models import DataSource, Disease, ImportBatch, ObservationVintage, Region, SimulationLabel, SimulationTruth


def observations_frame(
    db: Session,
    *,
    disease_id: str | None = None,
    region_id: str | None = None,
    metric: str | None = None,
) -> pd.DataFrame:
    stmt = select(
        ObservationVintage.disease_id,
        ObservationVintage.region_id,
        ObservationVintage.period_start,
        ObservationVintage.period_end,
        ObservationVintage.issued_at,
        ObservationVintage.metric,
        ObservationVintage.value,
        ObservationVintage.unit,
        ObservationVintage.source_id,
        ObservationVintage.quality_flag,
        ObservationVintage.import_batch_id,
    )
    if disease_id:
        stmt = stmt.where(ObservationVintage.disease_id == disease_id)
    if region_id:
        stmt = stmt.where(ObservationVintage.region_id == region_id)
    if metric:
        stmt = stmt.where(ObservationVintage.metric == metric)
    rows = db.execute(stmt).all()
    columns = [
        "disease_id",
        "region_id",
        "period_start",
        "period_end",
        "issued_at",
        "metric",
        "value",
        "unit",
        "source_id",
        "quality_flag",
        "import_batch_id",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def labels_frame(db: Session) -> pd.DataFrame:
    rows = db.scalars(select(SimulationLabel)).all()
    if not rows:
        return pd.DataFrame(columns=["region_id", "period_start", "period_end", "category", "note"])
    return pd.DataFrame(
        [
            {
                "region_id": row.region_id,
                "period_start": row.period_start,
                "period_end": row.period_end,
                "category": row.category,
                "note": row.note,
            }
            for row in rows
        ]
    )


def truth_frame(db: Session) -> pd.DataFrame:
    rows = db.scalars(select(SimulationTruth)).all()
    if not rows:
        return pd.DataFrame(columns=["region_id", "period_start", "period_end", "metric", "value"])
    return pd.DataFrame(
        [
            {
                "region_id": row.region_id,
                "period_start": row.period_start,
                "period_end": row.period_end,
                "metric": row.metric,
                "value": row.value,
            }
            for row in rows
        ]
    )


def data_mode_banner(db: Session) -> dict:
    batches = db.scalars(select(ImportBatch)).all()
    if not batches:
        return {"mode": "empty", "banner": EMPTY_BANNER, "is_synthetic": False}
    if all(batch.is_synthetic for batch in batches):
        return {"mode": "synthetic", "banner": SYNTHETIC_BANNER, "is_synthetic": True}
    if any(batch.is_synthetic for batch in batches):
        return {
            "mode": "mixed",
            "banner": f"{SYNTHETIC_BANNER} 同时存在导入数据。{IMPORTED_BANNER}",
            "is_synthetic": True,
        }
    return {"mode": "imported", "banner": IMPORTED_BANNER, "is_synthetic": False}


def _coverage_by_disease(db: Session) -> dict[str, dict]:
    """SQL aggregates only. Do not load every vintage into pandas."""
    coverage: dict[str, dict] = {}
    agg = db.execute(
        select(
            ObservationVintage.disease_id,
            func.count().label("n_rows"),
            func.count(func.distinct(ObservationVintage.region_id)).label("n_regions"),
            func.min(ObservationVintage.period_end).label("start"),
            func.max(ObservationVintage.period_end).label("end"),
            func.max(ObservationVintage.issued_at).label("updated_at"),
        ).group_by(ObservationVintage.disease_id)
    ).all()
    metrics = db.execute(select(ObservationVintage.disease_id, ObservationVintage.metric).distinct()).all()
    metrics_map: dict[str, set[str]] = {}
    for disease_id, metric in metrics:
        metrics_map.setdefault(str(disease_id), set()).add(str(metric))
    for row in agg:
        disease_id = str(row.disease_id)
        start = pd.Timestamp(row.start).date().isoformat() if row.start is not None else None
        end = pd.Timestamp(row.end).date().isoformat() if row.end is not None else None
        updated = pd.Timestamp(row.updated_at).isoformat(sep=" ", timespec="seconds") if row.updated_at is not None else None
        coverage[disease_id] = {
            "n_rows": int(row.n_rows),
            "n_regions": int(row.n_regions),
            "metrics": sorted(metrics_map.get(disease_id, set())),
            "start": start,
            "end": end,
            "updated_at": updated,
        }
    return coverage


def list_diseases(db: Session) -> list[dict]:
    coverage = _coverage_by_disease(db)
    items = []
    seen = set()
    for spec in DISEASE_SPECS.values():
        seen.add(spec.disease_id)
        row = db.get(Disease, spec.disease_id)
        payload = spec.as_dict()
        payload.update(
            {
                "id": spec.disease_id,
                "name": spec.name_zh,
                "description": row.description if row else spec.notes,
                "is_synthetic": spec.is_synthetic,
                "coverage": coverage.get(spec.disease_id),
                "recommendation": recommend_models(spec.disease_id, n_weeks=coverage.get(spec.disease_id, {}).get("n_rows", 0)),
            }
        )
        items.append(payload)
    for row in db.scalars(select(Disease)).all():
        if row.id in seen:
            continue
        items.append(
            {
                "id": row.id,
                "disease_id": row.id,
                "name": row.name,
                "name_zh": row.name,
                "description": row.description,
                "is_synthetic": row.is_synthetic,
                "status": "pending",
                "status_label": "数据库中有记录，注册表尚未收录",
                "coverage": coverage.get(row.id),
            }
        )
    return items


def region_payload(row: Region) -> dict:
    raw = dict(row.context_json or {})
    if row.population is not None:
        raw.setdefault("population", row.population)
    context = public_context(raw)
    return {
        "id": row.id,
        "name": row.name,
        "population": row.population,
        "country_id": raw.get("country_id"),
        "scenario": row.scenario,
        "note": row.note,
        "context": context,
        "profile": context.get("profile") or {},
    }


def list_regions(db: Session, disease_id: str | None = None) -> list[dict]:
    rows = [region_payload(row) for row in db.scalars(select(Region)).all()]
    if not disease_id:
        return rows
    frame = observations_frame(db, disease_id=disease_id)
    if frame.empty:
        return []
    allowed = set(frame["region_id"].astype(str))
    return [row for row in rows if row["id"] in allowed]


def region_context_map(db: Session) -> dict[str, dict]:
    mapping = {}
    for row in db.scalars(select(Region)).all():
        raw = dict(row.context_json or {})
        if row.population is not None:
            raw["population"] = row.population
        mapping[row.id] = raw
    return mapping


def list_sources(db: Session) -> list[dict]:
    return [
        {
            "id": row.id,
            "name": row.name,
            "url": row.url,
            "license": row.license,
            "update_note": row.update_note,
            "connector_status": row.connector_status,
        }
        for row in db.scalars(select(DataSource)).all()
    ]


def visible_observations(
    db: Session,
    *,
    disease_id: str,
    region_id: str | None,
    metric: str,
    cutoff: datetime | str,
    replay_mode: str = "vintage",
) -> pd.DataFrame:
    frame = observations_frame(db, disease_id=disease_id, region_id=region_id, metric=metric)
    return select_snapshot(frame, cutoff, replay_mode)


def completeness(db: Session, disease_id: str, region_id: str) -> dict:
    frame = observations_frame(db, disease_id=disease_id, region_id=region_id)
    if frame.empty:
        return {"weeks": [], "metrics": [], "matrix": [], "note": "尚无数据"}
    visible = latest_visible(frame, frame["issued_at"].max(), require_period_ended=False)
    metrics = sorted(visible["metric"].unique())
    weeks = sorted(pd.to_datetime(visible["period_end"]).unique())
    matrix = []
    for week in weeks:
        row = {"period_end": pd.Timestamp(week).date().isoformat()}
        for metric in metrics:
            subset = visible[(visible["metric"] == metric) & (pd.to_datetime(visible["period_end"]) == week)]
            if subset.empty:
                row[metric] = "absent"
            elif subset.iloc[0]["value"] is None or pd.isna(subset.iloc[0]["value"]):
                row[metric] = "missing"
            elif subset.iloc[0]["quality_flag"] == "provisional":
                row[metric] = "provisional"
            else:
                row[metric] = "complete"
        matrix.append(row)
    return {
        "weeks": [pd.Timestamp(item).date().isoformat() for item in weeks],
        "metrics": metrics,
        "matrix": matrix,
        "note": "缺失不等于 0。不同指标不能直接混合。",
        "has_multiple_vintages": has_multiple_vintages(frame),
    }


def overview(db: Session, disease_id: str, region_id: str, cutoff: datetime | str | None = None, metric: str | None = None) -> dict:
    spec = get_disease(disease_id)
    metric = metric or (spec.default_target if spec else "hospital_admissions")
    frame = observations_frame(db, disease_id=disease_id, region_id=region_id, metric=metric)
    banner = data_mode_banner(db)
    disease_payload = next((item for item in list_diseases(db) if item["id"] == disease_id), None)
    if frame.empty:
        return {
            "banner": banner,
            "series": [],
            "revision": {"estimable": False, "message": "无法估计修订过程"},
            "quality": {},
            "disease": disease_payload,
            "metric": metric,
            "status_label": None if spec is None else spec.status_label,
        }
    if cutoff is None:
        cutoff = frame["issued_at"].max()
    admissions = select_snapshot(frame[frame["metric"] == metric], cutoff, "vintage")
    index, values = prepare_weekly(admissions)
    series = []
    for stamp, value in zip(index, values):
        series.append(
            {
                "period_end": stamp.date().isoformat(),
                "value": None if value != value else float(value),
            }
        )
    revision = estimate_revision(frame[frame["metric"] == metric], cutoff)
    last_value = None
    for item in reversed(series):
        if item["value"] is not None:
            last_value = item["value"]
            break
    nowcast = nowcast_interval(last_value, revision) if last_value is not None else None
    quality = {
        "cutoff_time": pd.Timestamp(cutoff).isoformat(sep=" ", timespec="seconds"),
        "n_points": int(sum(item["value"] is not None for item in series)),
        "n_missing": int(sum(item["value"] is None for item in series)),
        "provisional_share": float(
            (admissions["quality_flag"] == "provisional").mean() if not admissions.empty else 0.0
        ),
        "has_multiple_vintages": has_multiple_vintages(frame),
        "trust_note": "数据可信程度依据版本完整性与质量标记，不是大流行概率。",
    }
    region = next((item for item in list_regions(db) if item["id"] == region_id), None)
    analog = match_values(values, context=None if region is None else (region.get("context") or {}).get("raw"))
    return {
        "banner": banner,
        "series": series,
        "revision": revision,
        "nowcast": nowcast,
        "quality": quality,
        "region": region,
        "disease": disease_payload,
        "metric": metric,
        "status_label": None if spec is None else spec.status_label,
        "analog": analog,
    }
