from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from aerp.core.geo import BOUNDARY_VERSION, boundary_payload, provenance
from aerp.db.models import GeoUnit, RegionGeoMapping
from aerp.db.session import get_db
from aerp.services.geo_catalog import ensure_geo_catalog

router = APIRouter()


@router.get("/geo/units")
def geo_units(level: str | None = None, q: str | None = None, db: Session = Depends(get_db)):
    ensure_geo_catalog(db)
    rows = list(db.scalars(select(GeoUnit)).all())
    if level:
        rows = [row for row in rows if row.level == level]
    if q:
        needle = q.lower()
        rows = [
            row
            for row in rows
            if needle in (row.name or "").lower()
            or needle in (row.name_en or "").lower()
            or needle in row.geo_id.lower()
            or needle in (row.country_code or "").lower()
            or needle in (row.region_code or "").lower()
        ]
    return {
        "boundary_version": BOUNDARY_VERSION,
        "n": len(rows),
        "items": [
            {
                "geo_id": row.geo_id,
                "name": row.name,
                "name_en": row.name_en,
                "level": row.level,
                "parent_geo_id": row.parent_geo_id,
                "country_code": row.country_code,
                "region_code": row.region_code,
                "has_polygon": row.has_polygon,
                "representative_point": row.representative_point,
                "extra": row.extra,
            }
            for row in rows
        ],
    }


@router.get("/geo/boundaries")
def geo_boundaries(level: str = "country", parent_geo_id: str | None = None, db: Session = Depends(get_db)):
    ensure_geo_catalog(db)
    payload = boundary_payload(level=level, parent_geo_id=parent_geo_id)
    payload["provenance"] = provenance()
    return payload


@router.get("/geo/mappings")
def geo_mappings(unmatched_only: bool = False, db: Session = Depends(get_db)):
    ensure_geo_catalog(db)
    rows = list(db.scalars(select(RegionGeoMapping)).all())
    if unmatched_only:
        rows = [row for row in rows if row.review_status != "reviewed"]
    return [
        {
            "source_id": row.source_id,
            "source_region_id": row.source_region_id,
            "geo_id": row.geo_id,
            "match_method": row.match_method,
            "review_status": row.review_status,
            "note": row.note,
        }
        for row in rows
    ]
