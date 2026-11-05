"""WHO FluNet / GISRS country-week laboratory detections.

This is virological surveillance (detections), not hospital admissions and not
clinical cases. US NHSN/FluSight admissions stay on a separate metric.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from aerp.config import ROOT, get_settings
from aerp.core.hashing import sha256_bytes, sha256_text
from aerp.core.timeutil import utcnow
from aerp.db.models import DataSource, Disease, ImportBatch, ObservationVintage, Region

FLUNET_PAGE = "https://www.who.int/tools/flunet/"
FLUNET_URL = "https://xmart-api-public.who.int/FLUMART/VIW_FNT?$format=csv"
SOURCE_ID = "who_flunet"
BATCH_PREFIX = "who-flunet-"
LICENSE = (
    "WHO FluNet / GISRS public country-week table; attribution required. "
    "Presentation of names/boundaries does not imply WHO opinion on legal status."
)
COVERAGE_NOTE = (
    "国家级实验室周检出，不是住院、不是临床病例、不是感染人数。"
    "流感用 INF_ALL，RSV 用 RSV 列。同一国家同一周多个来源时取检测标本数最多的一条，避免把哨点与非哨点相加。"
    "无该国该周记录保持未报告，不会把美国住院涂到其他国家。"
)
METRIC = "lab_detections"
FIXTURE_PATH = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "who_flunet_sample.csv"
USECOLS = [
    "COUNTRY_CODE",
    "COUNTRY_AREA_TERRITORY",
    "ISO_WEEKSTARTDATE",
    "ISO_YEAR",
    "ORIGIN_SOURCE",
    "SPEC_PROCESSED_NB",
    "INF_ALL",
    "RSV",
    "ISO2",
]


def connector_status(db: Session | None = None) -> dict:
    batch = None
    if db is not None:
        batch = db.scalars(
            select(ImportBatch).where(ImportBatch.source_id == SOURCE_ID).order_by(ImportBatch.imported_at.desc())
        ).first()
    return {
        "id": SOURCE_ID,
        "name": "WHO FluNet / GISRS 国家周实验室监测",
        "url": FLUNET_PAGE,
        "status": "available" if batch else "unverified",
        "license": LICENSE,
        "coverage": "国家/地区 ISO2；以实际导入的周为准",
        "metric_definition": "周流感检出 INF_ALL 与周 RSV 检出。不是住院。",
        "time_grain": "week",
        "release": "采集时点快照。源表无独立历史发布时间。",
        "note": COVERAGE_NOTE,
        "last_batch": None
        if batch is None
        else {"id": batch.id, "imported_at": batch.imported_at.isoformat(), "rows": batch.row_count},
    }


def _looks_like_csv(payload: bytes | None) -> bool:
    if not payload or len(payload) < 80:
        return False
    head = payload[:240].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        return False
    return b"COUNTRY_CODE" in payload[:800] or b"country_code" in payload[:800] or b"," in payload[:400]


def _get(url: str, timeout: int = 120) -> tuple[bytes | None, dict]:
    request = Request(url, headers={"User-Agent": "AERP-research/0.4"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read()
            status = int(getattr(response, "status", 200) or 200)
            if not _looks_like_csv(payload):
                return None, {"status": "error", "http_status": status, "message": "响应不是 FluNet CSV", "url": url}
            return payload, {"status": "success", "http_status": status, "url": url, "bytes": len(payload)}
    except HTTPError as exc:
        body = exc.read() if exc.fp else b""
        if _looks_like_csv(body):
            return body, {"status": "success", "http_status": exc.code, "url": url, "bytes": len(body)}
        return None, {"status": "error", "http_status": exc.code, "message": str(exc), "url": url}
    except (URLError, TimeoutError, OSError) as exc:
        return None, {"status": "error", "message": str(exc), "url": url}


def fetch_flunet_csv(*, force_refresh: bool = False, cache_dir: Path | None = None) -> tuple[bytes | None, dict]:
    cache = Path(cache_dir or (ROOT / "data" / "raw"))
    cache.mkdir(parents=True, exist_ok=True)
    cache_file = cache / "who_flunet.csv"
    ttl = max(0, int(get_settings().aerp_source_cache_hours)) * 3600
    cache_ok = cache_file.exists() and cache_file.stat().st_size > 1000
    cache_age = (time.time() - cache_file.stat().st_mtime) if cache_ok else None
    if cache_ok and not force_refresh and cache_age is not None and cache_age < ttl and _looks_like_csv(cache_file.read_bytes()):
        return cache_file.read_bytes(), {
            "status": "success",
            "source": "cache",
            "path": str(cache_file),
            "cache_age_seconds": cache_age,
            "vintage_kind": "采集时点快照",
        }
    payload, meta = _get(FLUNET_URL)
    if payload:
        cache_file.write_bytes(payload)
        meta.update({"source": "network", "sha256": sha256_bytes(payload), "vintage_kind": "采集时点快照"})
        return payload, meta
    if cache_ok and _looks_like_csv(cache_file.read_bytes()):
        return cache_file.read_bytes(), {
            "status": "success",
            "source": "stale_cache",
            "stale": True,
            "path": str(cache_file),
            "vintage_kind": "采集时点快照",
            "fetch": meta,
        }
    return None, {"status": "error", "fetch": meta, "vintage_kind": "采集时点快照"}


def parse_flunet_csv(payload: bytes, *, min_year: int | None = None, issued_at: datetime | None = None) -> pd.DataFrame:
    issued = pd.Timestamp(issued_at or utcnow()).tz_localize(None)
    year_floor = int(min_year if min_year is not None else utcnow().year - 2)
    wanted = {c.upper() for c in USECOLS}
    header = pd.read_csv(pd.io.common.BytesIO(payload), nrows=0)
    cols = [col for col in header.columns if str(col).strip().upper() in wanted]
    work = pd.read_csv(pd.io.common.BytesIO(payload), usecols=cols)
    work.columns = [str(col).strip().upper() for col in work.columns]
    if "ISO2" not in work.columns and "COUNTRY_CODE" not in work.columns:
        raise ValueError("FluNet 表缺少国家码")
    if "ISO_WEEKSTARTDATE" not in work.columns:
        raise ValueError("FluNet 表缺少 ISO_WEEKSTARTDATE")
    work["ISO_YEAR"] = pd.to_numeric(work.get("ISO_YEAR"), errors="coerce")
    work = work.loc[work["ISO_YEAR"].fillna(0) >= year_floor].copy()
    iso2 = work["ISO2"].astype(str).str.strip().str.upper() if "ISO2" in work.columns else pd.Series("", index=work.index)
    iso3 = work["COUNTRY_CODE"].astype(str).str.strip().str.upper() if "COUNTRY_CODE" in work.columns else pd.Series("", index=work.index)
    work["region_id"] = iso2.where(iso2.str.fullmatch(r"[A-Z]{2}"), iso3)
    work = work.loc[work["region_id"].str.fullmatch(r"[A-Z]{2,3}")].copy()
    work["week_start"] = pd.to_datetime(work["ISO_WEEKSTARTDATE"], errors="coerce")
    work = work.loc[work["week_start"].notna()].copy()
    work["period_end"] = work["week_start"] + pd.Timedelta(days=6)
    work["specimens"] = pd.to_numeric(work.get("SPEC_PROCESSED_NB"), errors="coerce").fillna(0)
    work["flu"] = pd.to_numeric(work.get("INF_ALL"), errors="coerce")
    work["rsv"] = pd.to_numeric(work.get("RSV"), errors="coerce")
    work = work.sort_values(["region_id", "week_start", "specimens"])
    picked = work.drop_duplicates(["region_id", "week_start"], keep="last")
    rows = []
    for item in picked.itertuples(index=False):
        start = pd.Timestamp(item.week_start).to_pydatetime()
        end = pd.Timestamp(item.period_end).to_pydatetime()
        if pd.notna(item.flu):
            rows.append(
                {
                    "disease_id": "influenza",
                    "region_id": str(item.region_id),
                    "period_start": start,
                    "period_end": end,
                    "issued_at": issued.to_pydatetime(),
                    "metric": METRIC,
                    "value": float(item.flu),
                    "unit": "detections",
                    "source_id": SOURCE_ID,
                    "quality_flag": "observed",
                }
            )
        if pd.notna(item.rsv):
            rows.append(
                {
                    "disease_id": "rsv",
                    "region_id": str(item.region_id),
                    "period_start": start,
                    "period_end": end,
                    "issued_at": issued.to_pydatetime(),
                    "metric": METRIC,
                    "value": float(item.rsv),
                    "unit": "detections",
                    "source_id": SOURCE_ID,
                    "quality_flag": "observed",
                }
            )
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
            ]
        )
    return pd.DataFrame(rows)


def import_flunet_observations(db: Session, frame: pd.DataFrame, *, filename: str, note: str, is_fixture: bool = False) -> dict:
    if frame is None or frame.empty:
        return {"status": "error", "message": "FluNet 解析后没有可导入的周检出。", "is_fixture": is_fixture}
    if db.get(DataSource, SOURCE_ID) is None:
        db.add(
            DataSource(
                id=SOURCE_ID,
                name="WHO FluNet GISRS",
                url=FLUNET_PAGE,
                license=LICENSE,
                update_note=COVERAGE_NOTE,
                connector_status="available",
            )
        )
        db.flush()
    for disease_id, name in (("influenza", "流感"), ("rsv", "RSV 感染")):
        if db.get(Disease, disease_id) is None:
            db.add(Disease(id=disease_id, name=name, description="WHO FluNet 实验室周检出。", is_synthetic=False))
    db.flush()
    for region_id in frame["region_id"].astype(str).unique():
        if db.get(Region, region_id) is None:
            db.add(Region(id=region_id, name=region_id, note="WHO FluNet ISO country code", scenario="who_flunet"))
    payload_hash = sha256_text(frame.to_json(orient="records", date_format="iso"))
    batch_id = f"{BATCH_PREFIX}{payload_hash[:12]}"
    existing = db.get(ImportBatch, batch_id)
    if existing:
        return {"status": "exists", "batch_id": batch_id, "n_rows": existing.row_count, "is_fixture": is_fixture}
    db.add(
        ImportBatch(
            id=batch_id,
            source_id=SOURCE_ID,
            file_hash=payload_hash,
            filename=filename,
            imported_at=utcnow(),
            row_count=int(len(frame)),
            note=note,
            is_synthetic=False,
        )
    )
    db.flush()
    existing_keys = {
        (str(row.disease_id), str(row.region_id), str(row.metric), pd.Timestamp(row.period_start), pd.Timestamp(row.issued_at))
        for row in db.execute(
            select(
                ObservationVintage.disease_id,
                ObservationVintage.region_id,
                ObservationVintage.metric,
                ObservationVintage.period_start,
                ObservationVintage.issued_at,
            ).where(ObservationVintage.source_id == SOURCE_ID)
        )
    }
    n = 0
    for row in frame.itertuples(index=False):
        key = (
            str(row.disease_id),
            str(row.region_id),
            str(row.metric),
            pd.Timestamp(row.period_start),
            pd.Timestamp(row.issued_at),
        )
        if key in existing_keys:
            continue
        db.add(
            ObservationVintage(
                disease_id=str(row.disease_id),
                region_id=str(row.region_id),
                period_start=pd.Timestamp(row.period_start).to_pydatetime(),
                period_end=pd.Timestamp(row.period_end).to_pydatetime(),
                issued_at=pd.Timestamp(row.issued_at).to_pydatetime(),
                metric=str(row.metric),
                value=None if pd.isna(row.value) else float(row.value),
                unit=str(row.unit),
                source_id=SOURCE_ID,
                quality_flag=str(row.quality_flag),
                import_batch_id=batch_id,
            )
        )
        existing_keys.add(key)
        n += 1
    if n == 0:
        db.rollback()
        return {"status": "up_to_date", "batch_id": batch_id, "n_rows": 0, "is_fixture": is_fixture}
    db.commit()
    return {
        "status": "success",
        "batch_id": batch_id,
        "n_rows": n,
        "n_regions": int(frame["region_id"].nunique()),
        "metrics": sorted(set(frame["metric"].astype(str))),
        "diseases": sorted(set(frame["disease_id"].astype(str))),
        "is_fixture": is_fixture,
        "is_synthetic": False,
        "note": note,
    }


def fetch_and_import(db: Session, *, cache_dir: Path | None = None, force_refresh: bool = False, min_year: int | None = None) -> dict:
    payload, meta = fetch_flunet_csv(force_refresh=force_refresh, cache_dir=cache_dir)
    if not payload:
        return {
            "status": "error",
            "connector": SOURCE_ID,
            "message": "未能获取 WHO FluNet。流感/RSV 国家层保持无数据，不会用美国住院填补。",
            "detail": meta,
        }
    try:
        frame = parse_flunet_csv(payload, min_year=min_year)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "message": f"FluNet 解析失败: {exc}", "detail": meta}
    imported = import_flunet_observations(
        db,
        frame,
        filename="who_flunet.csv",
        note=COVERAGE_NOTE,
        is_fixture=False,
    )
    imported["fetch"] = meta
    imported["checksum"] = sha256_bytes(payload)
    return imported


def import_fixture(db: Session, path: Path | None = None) -> dict:
    target = path or FIXTURE_PATH
    payload = Path(target).read_bytes()
    frame = parse_flunet_csv(payload, min_year=2020, issued_at=datetime(2024, 3, 20))
    return import_flunet_observations(
        db,
        frame,
        filename=Path(target).name,
        note="测试夹具，FluNet 列格式。不是完整全球监测。",
        is_fixture=True,
    )
