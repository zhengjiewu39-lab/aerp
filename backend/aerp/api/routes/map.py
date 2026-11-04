from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from aerp.db.session import get_db
from aerp.services.geo_catalog import ensure_geo_catalog
from aerp.services.map_data import coverage_report, map_observations, region_detail, stored_classification
from aerp.services.who_covid import connector_status as who_status, fetch_and_import

router = APIRouter()


@router.get("/map/coverage")
def map_coverage(disease_id: str, metric: str | None = None, parent_geo_id: str | None = None, db: Session = Depends(get_db)):
    ensure_geo_catalog(db)
    payload = coverage_report(db, disease_id=disease_id, metric=metric, parent_geo_id=parent_geo_id)
    payload["who_covid"] = who_status(db)
    return payload


@router.get("/map/observations")
def map_obs(
    disease_id: str,
    metric: str = "weekly_cases",
    period_end: str | None = None,
    time_mode: str = Query("unified", pattern="^(unified|latest)$"),
    view: str = "rate_per_100k",
    parent_geo_id: str | None = None,
    data_mode: str = "real",
    lock_period_end: str | None = None,
    class_mode: str = Query("quantile", pattern="^(quantile|absolute)$"),
    db: Session = Depends(get_db),
):
    ensure_geo_catalog(db)
    return map_observations(
        db,
        disease_id=disease_id,
        metric=metric,
        period_end=period_end,
        time_mode=time_mode,
        view=view,
        parent_geo_id=parent_geo_id,
        data_mode=data_mode,
        lock_period_end=lock_period_end,
        class_mode=class_mode,
    )


@router.get("/map/classification")
def map_class(disease_id: str, metric: str = "weekly_cases", db: Session = Depends(get_db)):
    return stored_classification(db, disease_id, metric)


@router.get("/map/regions/{geo_id}")
def map_region(
    geo_id: str,
    disease_id: str,
    metric: str = "weekly_cases",
    period_end: str | None = None,
    time_mode: str = "unified",
    view: str = "rate_per_100k",
    db: Session = Depends(get_db),
):
    ensure_geo_catalog(db)
    payload = region_detail(
        db, geo_id, disease_id=disease_id, metric=metric, period_end=period_end, time_mode=time_mode, view=view
    )
    if payload.get("error"):
        raise HTTPException(404, payload)
    return payload


@router.post("/map/who-covid/import")
def import_who(live: bool = False, db: Session = Depends(get_db)):
    ensure_geo_catalog(db)
    if not live:
        return {"status": "skipped", "message": "未请求 live=true，不会假装已接入。", "connector": who_status(db)}
    return fetch_and_import(db)
