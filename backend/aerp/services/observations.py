from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

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
    stmt = select(ObservationVintage)
    if disease_id:
        stmt = stmt.where(ObservationVintage.disease_id == disease_id)
    if region_id:
        stmt = stmt.where(ObservationVintage.region_id == region_id)
    if metric:
        stmt = stmt.where(ObservationVintage.metric == metric)
    rows = db.scalars(stmt).all()
    if not rows:
        return pd.DataFrame(
            columns=[
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
        )
    return pd.DataFrame(
        [
            {
                "disease_id": row.disease_id,
                "region_id": row.region_id,
                "period_start": row.period_start,
                "period_end": row.period_end,
                "issued_at": row.issued_at,
                "metric": row.metric,
                "value": row.value,
                "unit": row.unit,
                "source_id": row.source_id,
                "quality_flag": row.quality_flag,
                "import_batch_id": row.import_batch_id,
            }
            for row in rows
        ]
    )


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


def list_diseases(db: Session) -> list[dict]:
    return [
        {"id": row.id, "name": row.name, "description": row.description, "is_synthetic": row.is_synthetic}
        for row in db.scalars(select(Disease)).all()
    ]


def list_regions(db: Session) -> list[dict]:
    return [
        {
            "id": row.id,
            "name": row.name,
            "population": row.population,
            "scenario": row.scenario,
            "note": row.note,
        }
        for row in db.scalars(select(Region)).all()
    ]


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


def overview(db: Session, disease_id: str, region_id: str, cutoff: datetime | str | None = None) -> dict:
    frame = observations_frame(db, disease_id=disease_id, region_id=region_id)
    banner = data_mode_banner(db)
    if frame.empty:
        return {"banner": banner, "series": [], "revision": {"estimable": False, "message": "无法估计修订过程"}, "quality": {}}
    if cutoff is None:
        cutoff = frame["issued_at"].max()
    admissions = select_snapshot(frame[frame["metric"] == "hospital_admissions"], cutoff, "vintage")
    index, values = prepare_weekly(admissions)
    series = []
    for stamp, value in zip(index, values):
        series.append(
            {
                "period_end": stamp.date().isoformat(),
                "value": None if value != value else float(value),
            }
        )
    revision = estimate_revision(frame[frame["metric"] == "hospital_admissions"], cutoff)
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
    return {
        "banner": banner,
        "series": series,
        "revision": revision,
        "nowcast": nowcast,
        "quality": quality,
        "region": next((item for item in list_regions(db) if item["id"] == region_id), None),
        "disease": next((item for item in list_diseases(db) if item["id"] == disease_id), None),
    }
