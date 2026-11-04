"""Load GeoUnits, boundaries metadata, and explicit source-region mappings."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from aerp.core.geo import (
    BOUNDARY_VERSION,
    DISPUTED_NOTES,
    FIPS_TO_POSTAL,
    POSTAL_TO_FIPS,
    provenance,
    unit_records,
)
from aerp.db.models import GeoBoundary, GeoUnit, PopulationEstimate, RegionGeoMapping

_READY = False
_MAPPING_INDEX: dict[tuple[str, str], str] | None = None
_ISO3_INDEX: dict[str, str] | None = None


def ensure_geo_catalog(db: Session) -> dict:
    global _READY
    if _READY and db.get(GeoUnit, "iso3166-1:US") is not None:
        return {"n_units": None, "cached": True, "boundary_version": BOUNDARY_VERSION}
    units = unit_records()
    by_id = {}
    files = provenance().get("files") or {}
    for spec in units:
        geo_id = spec["geo_id"]
        by_id[geo_id] = spec
        row = db.get(GeoUnit, geo_id)
        payload = {
            "name": spec["name"],
            "name_en": spec["name_en"],
            "level": spec["level"],
            "parent_geo_id": None,
            "country_code": spec["country_code"],
            "region_code": spec["region_code"],
            "has_polygon": spec["has_polygon"],
            "representative_point": spec["representative_point"],
            "extra": {
                "iso2": spec.get("iso2"),
                "iso3": spec.get("iso3"),
                "fips": spec.get("fips"),
                "geometry_kind": spec.get("geometry_kind"),
                "point_note": spec.get("point_note"),
                "disputed_note": spec.get("disputed_note"),
            },
        }
        if row is None:
            db.add(GeoUnit(geo_id=geo_id, **payload))
        else:
            for key, value in payload.items():
                setattr(row, key, value)
    db.flush()
    for spec in units:
        if spec.get("parent_geo_id"):
            row = db.get(GeoUnit, spec["geo_id"])
            if row is not None:
                row.parent_geo_id = spec["parent_geo_id"]
    db.flush()
    for spec in units:
        geo_id = spec["geo_id"]
        file_ref = "ne_110m_admin_0_countries.min.geojson"
        checksum = (files.get("countries") or {}).get("sha256", "")
        if spec["level"] == "adm1":
            file_ref = "us_states_fips.min.geojson"
            checksum = (files.get("states") or {}).get("sha256", "")
        elif spec.get("representative_point"):
            file_ref = "small_country_representative_points.geojson"
            checksum = (files.get("points") or {}).get("sha256", "")
        existing_b = db.scalars(
            select(GeoBoundary).where(GeoBoundary.geo_id == geo_id, GeoBoundary.boundary_version == BOUNDARY_VERSION)
        ).first()
        if existing_b is None:
            db.add(
                GeoBoundary(
                    geo_id=geo_id,
                    boundary_version=BOUNDARY_VERSION,
                    geometry_kind=spec.get("geometry_kind") or "Unknown",
                    source="natural_earth_110m" if spec["level"] == "country" else "us_census_derived",
                    license="public domain",
                    file_ref=file_ref,
                    checksum=checksum or "",
                    fetched_at="2026-10-23",
                )
            )
        if spec.get("pop_est"):
            pop_row = db.scalars(
                select(PopulationEstimate).where(
                    PopulationEstimate.geo_id == geo_id,
                    PopulationEstimate.year == int(spec.get("pop_year") or 2019),
                    PopulationEstimate.source == "natural_earth_pop_est",
                )
            ).first()
            if pop_row is None:
                db.add(
                    PopulationEstimate(
                        geo_id=geo_id,
                        year=int(spec.get("pop_year") or 2019),
                        population=int(spec["pop_est"]),
                        source="natural_earth_pop_est",
                        version="ne_110m",
                    )
                )
    _ensure_code_mappings(db, by_id)
    db.commit()
    _READY = True
    return {
        "n_units": len(units),
        "boundary_version": BOUNDARY_VERSION,
        "n_polygons": sum(1 for item in units if item["has_polygon"]),
        "n_points": sum(1 for item in units if item["representative_point"]),
        "disputed": DISPUTED_NOTES,
    }


def reset_geo_catalog_cache() -> None:
    global _READY, _MAPPING_INDEX, _ISO3_INDEX
    _READY = False
    _MAPPING_INDEX = None
    _ISO3_INDEX = None


def _add_mapping(db: Session, *, source_id: str, source_region_id: str, geo_id: str, method: str, note: str = "") -> None:
    exists = db.scalars(
        select(RegionGeoMapping).where(
            RegionGeoMapping.source_id == source_id,
            RegionGeoMapping.source_region_id == source_region_id,
            RegionGeoMapping.geo_id == geo_id,
        )
    ).first()
    if exists:
        return
    db.add(
        RegionGeoMapping(
            source_id=source_id,
            source_region_id=source_region_id,
            geo_id=geo_id,
            match_method=method,
            review_status="reviewed",
            note=note,
        )
    )


def _ensure_code_mappings(db: Session, by_id: dict[str, dict]) -> None:
    for geo_id, spec in by_id.items():
        iso2 = spec.get("iso2")
        iso3 = spec.get("iso3")
        if spec.get("level") == "country":
            if iso2:
                _add_mapping(db, source_id="iso3166-1", source_region_id=iso2, geo_id=geo_id, method="iso2")
                _add_mapping(db, source_id="who_covid19", source_region_id=iso2, geo_id=geo_id, method="iso2")
                _add_mapping(db, source_id="internal", source_region_id=geo_id, geo_id=geo_id, method="geo_id")
            if iso3:
                _add_mapping(db, source_id="iso3166-1", source_region_id=iso3, geo_id=geo_id, method="iso3")
                _add_mapping(db, source_id="who_covid19", source_region_id=iso3, geo_id=geo_id, method="iso3")
        fips = spec.get("fips")
        if fips and spec.get("level") == "adm1":
            postal = FIPS_TO_POSTAL.get(fips)
            _add_mapping(db, source_id="fips", source_region_id=fips, geo_id=geo_id, method="fips")
            _add_mapping(db, source_id="flusight", source_region_id=fips, geo_id=geo_id, method="fips")
            _add_mapping(db, source_id="delphi_nhsn", source_region_id=fips, geo_id=geo_id, method="fips")
            if postal:
                _add_mapping(db, source_id="usps", source_region_id=postal, geo_id=geo_id, method="usps")
                _add_mapping(db, source_id="delphi_nhsn", source_region_id=postal.lower(), geo_id=geo_id, method="usps_lower")
                _add_mapping(db, source_id="flusight", source_region_id=f"US-{postal}", geo_id=geo_id, method="iso3166-2")
    us_id = "iso3166-1:US"
    if us_id in by_id:
        for code in ("US", "USA", "us"):
            _add_mapping(db, source_id="flusight", source_region_id=code, geo_id=us_id, method="iso2" if code.upper() == "US" else "iso3")
            _add_mapping(db, source_id="delphi_nhsn", source_region_id=code, geo_id=us_id, method="national_code")


def reviewed_mapping_index(db: Session) -> dict[tuple[str, str], str]:
    global _MAPPING_INDEX
    if _MAPPING_INDEX is None:
        rows = db.scalars(select(RegionGeoMapping).where(RegionGeoMapping.review_status == "reviewed")).all()
        _MAPPING_INDEX = {(row.source_id, row.source_region_id): row.geo_id for row in rows}
    return _MAPPING_INDEX


def resolve_geo_id(
    db: Session,
    source_region_id: str,
    *,
    source_id: str | None = None,
    mapping_index: dict[tuple[str, str], str] | None = None,
) -> tuple[str | None, str]:
    """Return (geo_id, method). Name fuzzy matches are never returned as official."""
    key = str(source_region_id).strip()
    if not key:
        return None, "empty"
    if db.get(GeoUnit, key):
        return key, "geo_id"
    mappings = mapping_index if mapping_index is not None else reviewed_mapping_index(db)
    if source_id and (source_id, key) in mappings:
        return mappings[(source_id, key)], "source_code"
    if source_id and (source_id, key.upper()) in mappings:
        return mappings[(source_id, key.upper())], "source_code"
    country_id = f"iso3166-1:{key.upper()}"
    if len(key) == 2 and db.get(GeoUnit, country_id):
        return country_id, "iso2"
    upper = key.upper()
    if len(upper) == 2:
        geo_id = f"iso3166-1:{upper}"
        if db.get(GeoUnit, geo_id):
            return geo_id, "iso2"
    unit = _unit_by_iso3(db, upper) if len(upper) == 3 and upper.isalpha() else None
    if unit:
        return unit.geo_id, "iso3"
    if upper in POSTAL_TO_FIPS:
        fips = POSTAL_TO_FIPS[upper]
        geo_id = f"iso3166-2:US-{fips}"
        if db.get(GeoUnit, geo_id):
            return geo_id, "usps"
    if upper.isdigit():
        fips = upper.zfill(2) if len(upper) <= 2 else upper[:2]
        geo_id = f"iso3166-2:US-{fips}"
        if db.get(GeoUnit, geo_id):
            return geo_id, "fips"
    return None, "unmatched"


def _iso3_index(db: Session) -> dict[str, str]:
    global _ISO3_INDEX
    if _ISO3_INDEX is None:
        index: dict[str, str] = {}
        for row in db.scalars(select(GeoUnit).where(GeoUnit.level == "country")).all():
            extra = row.extra or {}
            code = str(extra.get("iso3") or "").upper()
            if code:
                index[code] = row.geo_id
        _ISO3_INDEX = index
    return _ISO3_INDEX


def _unit_by_iso3(db: Session, iso3: str) -> GeoUnit | None:
    geo_id = _iso3_index(db).get(iso3)
    return db.get(GeoUnit, geo_id) if geo_id else None


def name_candidates(db: Session, source_region_id: str, limit: int = 5) -> list[dict]:
    needle = source_region_id.strip().lower()
    if len(needle) < 3:
        return []
    hits = []
    for row in db.scalars(select(GeoUnit)).all():
        names = {str(row.name or "").lower(), str(row.name_en or "").lower()}
        if needle in names or any(needle in item for item in names if item):
            hits.append(
                {
                    "geo_id": row.geo_id,
                    "name": row.name,
                    "match_method": "name_fuzzy_candidate",
                    "review_status": "pending",
                    "note": "名称模糊匹配只能作为待确认候选，不能直接用于正式地图着色。",
                }
            )
        if len(hits) >= limit:
            break
    return hits
