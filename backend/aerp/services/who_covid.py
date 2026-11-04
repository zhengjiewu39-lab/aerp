"""WHO COVID-19 national daily reports, aggregated to ISO weeks.

This is a reported-case surveillance series, not infections, admissions, or
a complete global census. Countries absent from a week stay unreported.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from aerp.config import ROOT
from aerp.core.hashing import sha256_bytes, sha256_text
from aerp.core.timeutil import utcnow
from aerp.db.models import DataSource, Disease, ImportBatch, ObservationVintage, Region

WHO_URL = "https://data.humdata.org/dataset/coronavirus-covid-19-cases-and-deaths/resource/2ac6c3c0-76fa-4486-9ad0-9aa9e253b78d/download/who-covid-19-global-data.csv"
WHO_PAGE = "https://data.who.int/dashboards/covid19/data"
SOURCE_ID = "who_covid19"
BATCH_PREFIX = "who-covid19-"
DISEASE_ID = "covid19"
LICENSE = "WHO COVID-19 dashboard data; attribution required. Presentation of boundaries/names does not imply WHO opinion on legal status."
COVERAGE_NOTE = (
    "国家级报告新增病例与死亡。不是住院，不是感染人数，不是确诊与疑似的统一口径。"
    "WHO 通用接口并不覆盖本系统全部病种。本连接器只用于 COVID-19 国家周汇总。"
    "无该国该周记录则保持未报告，不会补 0。"
)

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "who_covid_weekly_sample.csv"


def connector_status(db: Session | None = None) -> dict:
    batch = None
    if db is not None:
        batch = db.scalars(select(ImportBatch).where(ImportBatch.source_id == SOURCE_ID).order_by(ImportBatch.imported_at.desc())).first()
    return {
        "id": SOURCE_ID,
        "name": "WHO COVID-19 global daily data (weekly aggregate)",
        "url": WHO_PAGE,
        "status": "available" if batch else "unverified",
        "license": LICENSE,
        "coverage": "ISO2 国家；以实际导入的国家/周为准",
        "metric_definition": "周新增报告病例（New_cases 按流行病学周日–周六求和）；死亡单独指标",
        "time_grain": "week",
        "release": "WHO 日更新表；本系统按导入日作为 issued_at",
        "revision": "同一周期重复导入保留新批次，地图使用截止时最新版本，不把多版本相加",
        "update_frequency": "源表按日；本系统按需拉取",
        "note": COVERAGE_NOTE,
        "last_batch": None if batch is None else {"id": batch.id, "imported_at": batch.imported_at.isoformat(), "rows": batch.row_count},
    }


def _get(url: str, timeout: int = 60) -> tuple[bytes | None, dict]:
    request = Request(url, headers={"User-Agent": "AERP-research/0.4"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read()
            return payload, {"status": "success", "http_status": getattr(response, "status", 200)}
    except HTTPError as exc:
        return None, {"status": "error", "http_status": exc.code, "message": str(exc)}
    except (URLError, TimeoutError, OSError) as exc:
        return None, {"status": "error", "message": str(exc)}


def _normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    rename = {column: column.strip() for column in frame.columns}
    work = frame.rename(columns=rename)
    aliases = {
        "Date_reported": "date",
        "Country_code": "country_code",
        "Country": "country",
        "WHO_region": "who_region",
        "New_cases": "new_cases",
        "Cumulative_cases": "cumulative_cases",
        "New_deaths": "new_deaths",
        "Cumulative_deaths": "cumulative_deaths",
    }
    work = work.rename(columns={key: value for key, value in aliases.items() if key in work.columns})
    return work


def weekly_from_daily(frame: pd.DataFrame) -> pd.DataFrame:
    work = _normalize_columns(frame)
    required = {"date", "country_code", "new_cases"}
    missing = required - set(work.columns)
    if missing:
        raise ValueError(f"WHO 表缺少字段: {sorted(missing)}")
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work = work.dropna(subset=["date"])
    work["country_code"] = work["country_code"].astype(str).str.strip().str.upper()
    work = work.loc[work["country_code"].str.len() == 2].copy()
    # Epidemiology week Sunday-Saturday, matching AERP calendar.
    weekday = work["date"].dt.weekday  # Mon=0
    week_end = work["date"] + pd.to_timedelta((5 - weekday) % 7, unit="D")
    work["period_end"] = week_end.dt.normalize()
    work["period_start"] = work["period_end"] - pd.Timedelta(days=6)
    work["new_cases"] = pd.to_numeric(work["new_cases"], errors="coerce")
    work["new_deaths"] = pd.to_numeric(work.get("new_deaths"), errors="coerce") if "new_deaths" in work.columns else pd.NA
    grouped = (
        work.groupby(["country_code", "period_start", "period_end"], dropna=False)
        .agg(
            new_cases=("new_cases", lambda s: s.sum(min_count=1)),
            new_deaths=("new_deaths", lambda s: s.sum(min_count=1)),
            n_days=("date", "nunique"),
            n_case_days=("new_cases", lambda s: int(s.notna().sum())),
            country=("country", "last"),
        )
        .reset_index()
    )
    grouped["cases_quality"] = [
        "unreported" if n == 0 else ("partial" if n < 4 else "reported")
        for n in grouped["n_case_days"]
    ]
    return grouped


def observations_from_weekly(weekly: pd.DataFrame, *, issued_at: datetime) -> pd.DataFrame:
    rows = []
    for item in weekly.itertuples(index=False):
        region_id = str(item.country_code)
        common = {
            "disease_id": DISEASE_ID,
            "region_id": region_id,
            "period_start": item.period_start,
            "period_end": item.period_end,
            "issued_at": issued_at,
            "source_id": SOURCE_ID,
            "quality_flag": item.cases_quality,
        }
        rows.append({**common, "metric": "weekly_cases", "value": None if pd.isna(item.new_cases) else float(item.new_cases), "unit": "cases"})
        if not pd.isna(item.new_deaths):
            rows.append({**common, "metric": "weekly_deaths", "value": float(item.new_deaths), "unit": "deaths"})
    return pd.DataFrame(rows)


def parse_who_csv(payload: bytes, *, issued_at: datetime | None = None, max_weeks: int | None = 52) -> pd.DataFrame:
    head = payload[:200].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        raise ValueError("下载结果是 HTML 而不是 CSV。旧 covid19.who.int 直链已失效，需使用 HDX/WHO 数据页上的 CSV。")
    text = payload.decode("utf-8-sig", errors="replace")
    frame = pd.read_csv(io.StringIO(text))
    weekly = weekly_from_daily(frame)
    if max_weeks and not weekly.empty:
        reported = weekly.loc[weekly["n_case_days"] > 0]
        last = reported["period_end"].max() if not reported.empty else weekly["period_end"].max()
        weekly = weekly.loc[weekly["period_end"] > last - pd.Timedelta(weeks=max_weeks)].copy()
    issued = issued_at or utcnow()
    return observations_from_weekly(weekly, issued_at=issued)


def import_who_observations(db: Session, frame: pd.DataFrame, *, filename: str, note: str, is_fixture: bool = False) -> dict:
    if frame is None or frame.empty:
        return {"status": "error", "message": "没有可导入的 WHO 周观测"}
    ensure_covid_disease(db)
    source = db.get(DataSource, SOURCE_ID)
    if source is None:
        db.add(
            DataSource(
                id=SOURCE_ID,
                name="WHO COVID-19 global data",
                url=WHO_URL,
                license=LICENSE,
                update_note=COVERAGE_NOTE,
                connector_status="available",
            )
        )
        db.flush()
    else:
        source.connector_status = "available"
        source.update_note = COVERAGE_NOTE
    names = {}
    if "country" in frame.columns:
        pass
    for region_id, group in frame.groupby("region_id"):
        names[str(region_id)] = str(region_id)
        if db.get(Region, str(region_id)) is None:
            db.add(Region(id=str(region_id), name=str(region_id), note="WHO ISO2 country code", scenario="who_covid19"))
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
    n = 0
    for row in frame.itertuples(index=False):
        db.add(
            ObservationVintage(
                disease_id=row.disease_id,
                region_id=str(row.region_id),
                period_start=pd.Timestamp(row.period_start).to_pydatetime(),
                period_end=pd.Timestamp(row.period_end).to_pydatetime(),
                issued_at=pd.Timestamp(row.issued_at).to_pydatetime(),
                metric=row.metric,
                value=None if pd.isna(row.value) else float(row.value),
                unit=row.unit,
                source_id=SOURCE_ID,
                quality_flag=row.quality_flag,
                import_batch_id=batch_id,
            )
        )
        n += 1
    db.commit()
    return {
        "status": "success",
        "batch_id": batch_id,
        "n_rows": n,
        "n_regions": int(frame["region_id"].nunique()),
        "metrics": sorted(set(frame["metric"].astype(str))),
        "is_fixture": is_fixture,
        "is_synthetic": False,
        "note": note,
    }


def ensure_covid_disease(db: Session) -> None:
    row = db.get(Disease, DISEASE_ID)
    if row is None:
        db.add(Disease(id=DISEASE_ID, name="COVID-19", description="COVID-19。WHO 国家病例与可选 NHSN 住院分开。", is_synthetic=False))
        db.flush()


def fetch_and_import(db: Session, *, cache_dir: Path | None = None, max_weeks: int = 52) -> dict:
    cache = Path(cache_dir or (ROOT / "data" / "raw"))
    cache.mkdir(parents=True, exist_ok=True)
    cache_file = cache / "who_covid19_global.csv"
    payload = None
    meta = {}
    if cache_file.exists() and cache_file.stat().st_size > 1000:
        payload = cache_file.read_bytes()
        meta = {"status": "success", "source": "cache", "path": str(cache_file)}
    else:
        payload, meta = _get(WHO_URL)
        if payload:
            cache_file.write_bytes(payload)
            meta["source"] = "network"
            meta["sha256"] = sha256_bytes(payload)
    if not payload:
        return {
            "status": "error",
            "connector": SOURCE_ID,
            "message": "未能获取 WHO COVID-19 全球表。地图国家层保持无数据，不使用演示数字填补。",
            "detail": meta,
        }
    try:
        frame = parse_who_csv(payload, max_weeks=max_weeks)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "message": f"WHO 表解析失败: {exc}", "detail": meta}
    imported = import_who_observations(
        db,
        frame,
        filename="WHO-COVID-19-global-data.csv",
        note=f"WHO 国家日报告聚合为周。{COVERAGE_NOTE}",
        is_fixture=False,
    )
    imported["fetch"] = meta
    imported["checksum"] = sha256_bytes(payload)
    return imported


def import_fixture(db: Session, path: Path | None = None) -> dict:
    target = path or FIXTURE_PATH
    payload = Path(target).read_bytes()
    frame = parse_who_csv(payload, issued_at=datetime(2024, 3, 20), max_weeks=None)
    return import_who_observations(
        db,
        frame,
        filename=Path(target).name,
        note="测试夹具，WHO 列格式。不是完整全球监测，不得当作实时疫情。",
        is_fixture=True,
    )
