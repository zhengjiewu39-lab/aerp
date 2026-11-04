"""Local geographic catalog: Natural Earth countries, US states, and search points."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from aerp.core.hashing import sha256_bytes

GEO_DIR = Path(__file__).resolve().parents[1] / "data" / "geo"
COUNTRY_FILE = GEO_DIR / "ne_110m_admin_0_countries.min.geojson"
POINT_FILE = GEO_DIR / "small_country_representative_points.geojson"
STATE_FILE = GEO_DIR / "us_states_fips.min.geojson"
PROVENANCE_FILE = GEO_DIR / "provenance.json"

BOUNDARY_VERSION = "ne_110m_admin0-2024 + us-states-fips + small-points"

FIPS_TO_POSTAL = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT",
    "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL",
    "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD",
    "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE",
    "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV",
    "55": "WI", "56": "WY", "72": "PR",
}
POSTAL_TO_FIPS = {code: fips for fips, code in FIPS_TO_POSTAL.items()}

DISPUTED_NOTES = {
    "ne:CNX": "北塞浦路斯：Natural Earth 单独绘出，无稳定 ISO 代码。不代表本系统承认其地位。",
    "ne:SOL": "索马里兰：Natural Earth 单独绘出，无稳定 ISO 代码。不代表本系统承认其地位。",
    "iso3166-1:XK": "科索沃：源数据 ISO 为 -99，例外映射为 XK。ISO 地位有争议。",
    "iso3166-1:TW": "台湾：若 110m 面状缺失则用代表点；边界按 Natural Earth 处理。",
    "iso3166-1:PS": "巴勒斯坦：代表点定位，不表达最终边界主张。",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def provenance() -> dict[str, Any]:
    payload = _load_json(PROVENANCE_FILE)
    payload["boundary_version"] = BOUNDARY_VERSION
    payload["files"] = {
        "countries": _file_meta(COUNTRY_FILE),
        "points": _file_meta(POINT_FILE),
        "states": _file_meta(STATE_FILE),
    }
    return payload


def _file_meta(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": str(path.name),
        "bytes": len(data),
        "sha256": sha256_bytes(data),
        "exists": path.exists(),
    }


@lru_cache(maxsize=1)
def country_collection() -> dict[str, Any]:
    return _load_json(COUNTRY_FILE)


@lru_cache(maxsize=1)
def point_collection() -> dict[str, Any]:
    return _load_json(POINT_FILE)


@lru_cache(maxsize=1)
def state_collection() -> dict[str, Any]:
    return _load_json(STATE_FILE)


def iter_features(level: str = "country") -> list[dict[str, Any]]:
    if level == "country":
        return list(country_collection().get("features") or []) + list(point_collection().get("features") or [])
    if level == "adm1":
        return list(state_collection().get("features") or [])
    return iter_features("country") + iter_features("adm1")


def feature_index() -> dict[str, dict[str, Any]]:
    return {str(item["properties"]["geo_id"]): item for item in iter_features("all")}


def boundary_payload(*, level: str, parent_geo_id: str | None = None) -> dict[str, Any]:
    if level == "adm1":
        features = list(state_collection().get("features") or [])
        if parent_geo_id:
            features = [item for item in features if item["properties"].get("parent_geo_id") == parent_geo_id]
        available = bool(features)
        return {
            "level": level,
            "parent_geo_id": parent_geo_id,
            "boundary_version": BOUNDARY_VERSION,
            "available": available,
            "message": None if available else "没有可靠的一级行政区边界，不会把国家数值复制到省州。",
            "geojson": {"type": "FeatureCollection", "features": features},
            "n_features": len(features),
            "provenance": provenance(),
        }
    features = list(country_collection().get("features") or [])
    points = list(point_collection().get("features") or [])
    return {
        "level": "country",
        "parent_geo_id": None,
        "boundary_version": BOUNDARY_VERSION,
        "available": True,
        "message": "国家级面状边界来自 Natural Earth 110m；未包含的小岛/小型国家以代表点或列表访问。",
        "geojson": {"type": "FeatureCollection", "features": features},
        "points": {"type": "FeatureCollection", "features": points},
        "n_features": len(features),
        "n_points": len(points),
        "provenance": provenance(),
        "projection": {
            "name": "Web Mercator (EPSG:3857)",
            "polar_limit": "约 ±85° 以外面积严重失真，不用于极区流行病学结论。",
        },
    }


def unit_records() -> list[dict[str, Any]]:
    rows = []
    for feature in iter_features("all"):
        props = feature["properties"]
        rows.append(
            {
                "geo_id": props["geo_id"],
                "name": props.get("name"),
                "name_en": props.get("name_en") or props.get("name"),
                "level": props.get("level"),
                "parent_geo_id": props.get("parent_geo_id"),
                "country_code": props.get("iso2"),
                "region_code": props.get("fips") or props.get("iso2"),
                "iso2": props.get("iso2"),
                "iso3": props.get("iso3"),
                "fips": props.get("fips"),
                "has_polygon": not bool(props.get("representative_point")),
                "representative_point": bool(props.get("representative_point")),
                "pop_est": props.get("pop_est"),
                "pop_year": props.get("pop_year"),
                "geometry_kind": props.get("geometry_kind"),
                "point_note": props.get("point_note"),
                "disputed_note": DISPUTED_NOTES.get(props["geo_id"]),
            }
        )
    return rows
