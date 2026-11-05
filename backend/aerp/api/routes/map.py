from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from aerp.db.session import get_db
from aerp.services.geo_catalog import ensure_geo_catalog
from aerp.services.map_data import coverage_report, map_observations, region_detail, stored_classification
from aerp.services.comparability_replay import ranking_attribution
from aerp.services.who_covid import connector_status as who_status, fetch_and_import
from aerp.services.who_flunet import connector_status as flunet_status, fetch_and_import as fetch_flunet
from aerp.services.delphi import connector_status as nhsn_status, import_nhsn_observations
from aerp.services.flusight import import_flusight_map_observations
from aerp.services.dengue import check_dengue_sources, adapter_status as dengue_status

router = APIRouter()


@router.get("/map/coverage")
def map_coverage(disease_id: str, metric: str | None = None, parent_geo_id: str | None = None, db: Session = Depends(get_db)):
    ensure_geo_catalog(db)
    payload = coverage_report(db, disease_id=disease_id, metric=metric, parent_geo_id=parent_geo_id)
    payload["who_covid"] = who_status(db)
    payload["who_flunet"] = flunet_status(db)
    payload["nhsn"] = nhsn_status()
    payload["dengue"] = dengue_status()
    return payload


@router.get("/map/observations")
def map_obs(
    disease_id: str,
    metric: str = "weekly_cases",
    period_end: str | None = None,
    time_mode: str = Query("unified", pattern="^(unified|latest)$"),
    view: str = "count",
    parent_geo_id: str | None = None,
    data_mode: str = Query("real", pattern="^(real|demo)$"),
    lock_period_end: str | None = None,
    source_id: str | None = None,
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
        source_id=source_id,
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
    view: str = "count",
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
def import_who(live: bool = False, force_refresh: bool = False, db: Session = Depends(get_db)):
    ensure_geo_catalog(db)
    if not live:
        return {"status": "skipped", "message": "未请求 live=true，不会假装已接入。", "connector": who_status(db)}
    return fetch_and_import(db, force_refresh=force_refresh)


@router.post("/map/who-flunet/import")
def import_flunet(live: bool = False, force_refresh: bool = False, db: Session = Depends(get_db)):
    ensure_geo_catalog(db)
    if not live:
        return {"status": "skipped", "message": "未请求 live=true，不会假装已接入。", "connector": flunet_status(db)}
    return fetch_flunet(db, force_refresh=force_refresh)


@router.post("/map/nhsn/import")
def import_nhsn(live: bool = False, disease_id: str = "influenza", db: Session = Depends(get_db)):
    ensure_geo_catalog(db)
    if not live:
        return {"status": "skipped", "message": "未请求 live=true，不会假装已接入。", "connector": nhsn_status()}
    return import_nhsn_observations(db, disease_id=disease_id)


@router.post("/map/flusight/import")
def import_flusight(live: bool = False, kind: str = "snapshot", db: Session = Depends(get_db)):
    ensure_geo_catalog(db)
    if not live:
        return {"status": "skipped", "message": "未请求 live=true，不会假装已接入。"}
    return import_flusight_map_observations(db, kind=kind)


@router.post("/map/dengue/check")
def dengue_check(live: bool = False):
    return check_dengue_sources(live=live)


@router.get("/map/comparability-replay")
def comparability_replay(
    disease_id: str,
    metric: str = "weekly_cases",
    period_end: str | None = None,
    previous_period_end: str | None = None,
    view: str = "count",
    data_mode: str = Query("real", pattern="^(real|demo)$"),
    parent_geo_id: str | None = None,
    db: Session = Depends(get_db),
):
    ensure_geo_catalog(db)
    return ranking_attribution(
        db,
        disease_id=disease_id,
        metric=metric,
        period_end=period_end,
        previous_period_end=previous_period_end,
        view=view,
        data_mode=data_mode,
        parent_geo_id=parent_geo_id,
    )
