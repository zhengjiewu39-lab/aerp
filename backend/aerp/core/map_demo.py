"""Labeled country-level map playback series. Not real surveillance."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from aerp.core.geo import unit_records

SOURCE_ID = "map_demo"
BATCH_ID = "map_demo-seed-v2"
METRIC = "weekly_cases"
NOTE = "地图播放演示数据。固定种子合成，不是真实国家监测，不能写入论文真实结果。"

# Skip COVID: the map uses WHO weekly cases when imported.
MAP_DEMO_DISEASES = (
    "influenza",
    "rsv",
    "dengue",
    "measles",
    "synth-ari",
    "smallpox",
    "plague",
    "sars",
)

SOUTH = {
    "AR", "AU", "BO", "BR", "BW", "CL", "ID", "LS", "MG", "MZ", "NA", "NZ",
    "PE", "PY", "SZ", "UY", "ZA", "ZW", "AO", "TZ", "KE", "ZM",
}

# Spatial clusters so playback looks like moving burden, not one global flash.
CLUSTERS = {
    "influenza": ("US", "CA", "GB", "FR", "DE", "IT", "ES", "PL", "SE", "JP", "KR", "CN"),
    "rsv": ("US", "GB", "FR", "DE", "NL", "BE", "AU", "NZ", "JP", "CA"),
    "dengue": ("BR", "CO", "MX", "PE", "IN", "TH", "VN", "PH", "ID", "MY", "NG"),
    "measles": ("IN", "PK", "NG", "CD", "ET", "AF", "YE", "SO"),
    "synth-ari": ("US", "MX", "BR", "GB", "DE", "IN", "CN", "ZA"),
    "smallpox": ("IN", "BD", "ET", "SO", "AF"),
    "plague": ("MG", "CD", "PE", "US", "CN"),
    "sars": ("CN", "HK", "TW", "SG", "VN", "CA"),
}


@dataclass
class MapDemoBundle:
    observations: pd.DataFrame
    n_weeks: int
    n_countries: int
    note: str = NOTE


def _country_units(limit: int | None = None) -> list[dict]:
    rows = [
        item
        for item in unit_records()
        if item.get("level") == "country" and item.get("has_polygon") and item.get("iso2") and item.get("iso2") != "-99"
    ]
    rows.sort(key=lambda item: -(item.get("pop_est") or 0))
    if limit is not None:
        picked = rows[:limit]
        need = {code for codes in CLUSTERS.values() for code in codes}
        have = {item["iso2"] for item in picked}
        extras = [item for item in rows if item["iso2"] in need - have]
        return picked + extras
    return rows


def _wave(n: int, peak_week: int, width: int, height: float, rng: np.random.Generator) -> np.ndarray:
    weeks = np.arange(n)
    sigma = max(1.5, width / 2.5)
    curve = height * np.exp(-0.5 * ((weeks - peak_week) / sigma) ** 2)
    noise = rng.normal(1.0, 0.12, size=n)
    return np.clip(curve * np.maximum(0.4, noise), 0, None)


def generate_map_demo(seed: int = 42, n_weeks: int = 40, n_countries: int | None = None) -> MapDemoBundle:
    rng = np.random.default_rng(int(seed))
    units = _country_units(n_countries)
    ends = pd.date_range("2023-01-07", periods=n_weeks, freq="7D")
    rows: list[dict] = []
    rare = {"smallpox", "plague", "sars"}
    for disease_id in MAP_DEMO_DISEASES:
        cluster = set(CLUSTERS.get(disease_id, ()))
        for unit in units:
            iso2 = unit["iso2"]
            south = iso2 in SOUTH
            pop = float(unit.get("pop_est") or 1_000_000)
            in_cluster = iso2 in cluster
            if disease_id == "dengue":
                scale = 1.2 if in_cluster else (0.45 if rng.random() > 0.15 else 0.08)
            elif disease_id == "measles":
                scale = 1.1 if in_cluster else (0.4 if rng.random() > 0.35 else 0.05)
            elif disease_id in rare:
                scale = 1.3 if in_cluster else (0.12 if rng.random() > 0.55 else 0.0)
            else:
                scale = 1.2 if in_cluster else 0.45
            peak = 18 if south else 8
            if disease_id == "dengue":
                peak = 12 if not south else 6
            if disease_id == "sars":
                peak = 10
            height = (30 + rng.random() * 70) * max(scale, 0.04) * (pop / 25_000_000) ** 0.42
            width = 7 + int(rng.integers(0, 5))
            series = _wave(n_weeks, peak, width, height, rng)
            if scale > 0:
                series = np.maximum(series, height * 0.18)
            if disease_id in rare and in_cluster:
                series = _wave(n_weeks, int(rng.integers(6, 20)), 6, height * 1.4, rng)
                series = np.maximum(series, height * 0.1)
            for stamp, value in zip(ends, series):
                count = float(int(round(value)))
                if scale == 0.0 and count == 0:
                    quality = "unreported"
                    stored = None
                else:
                    quality = "synthetic"
                    stored = count
                rows.append(
                    {
                        "disease_id": disease_id,
                        "region_id": unit["geo_id"],
                        "period_start": stamp - pd.Timedelta(days=6),
                        "period_end": stamp,
                        "issued_at": stamp + pd.Timedelta(days=2),
                        "metric": METRIC,
                        "value": stored,
                        "unit": "count",
                        "source_id": SOURCE_ID,
                        "quality_flag": quality,
                    }
                )
    frame = pd.DataFrame(rows)
    return MapDemoBundle(observations=frame, n_weeks=n_weeks, n_countries=len(units), note=NOTE)
