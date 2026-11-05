"""WHO COVID-19 national daily reports, aggregated to ISO weeks.

This is a reported-case surveillance series, not infections, admissions, or
a complete global census. Countries absent from a week stay unreported.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from aerp.config import ROOT, get_settings
from aerp.core.hashing import sha256_bytes, sha256_text
from aerp.core.timeutil import utcnow
from aerp.db.models import DataSource, Disease, ImportBatch, ObservationVintage, Region

WHO_PAGE = "https://data.who.int/dashboards/covid19/data"
# 官方看板机器可读文件目前是日表。周定义为流行病学周日–周六，由本连接器按官方日表求和。
# HDX 常返回 202/HTML，放在最后。
WHO_CANDIDATE_URLS = (
    "https://srhdpeuwpubsa.blob.core.windows.net/whdh/COVID/WHO-COVID-19-global-daily-data.csv",
    "https://srhdpeuwpubsa.blob.core.windows.net/whdh/COVID/WHO-COVID-19-global-data.csv",
    "https://covid19.who.int/WHO-COVID-19-global-data.csv",
    "https://data.humdata.org/dataset/coronavirus-covid-19-cases-and-deaths/resource/2ac6c3c0-76fa-4486-9ad0-9aa9e253b78d/download/who-covid-19-global-data.csv",
)
WHO_URL = WHO_CANDIDATE_URLS[0]
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
        "name": "WHO COVID-19 dashboard（官方日表按周日–周六周汇总）",
        "url": WHO_PAGE,
        "status": "available" if batch else "unverified",
        "license": LICENSE,
        "coverage": "ISO2 国家；以实际导入的国家/周为准",
        "metric_definition": "官方可下载文件为日新增。本系统按流行病学周日–周六求和得到周病例与周死亡；若源表已是周表则直接使用。负向订正保留原值。",
        "time_grain": "week",
        "release": "采集时点快照：源表无独立历史发布时间时，issued_at 为抓取/导入时点，不能冒充官方历史版本或实时回放。",
        "revision": "内容哈希相同则不新建批次。有多次采集才可做订正对照。",
        "update_frequency": "源表按日；本系统按需拉取",
        "note": COVERAGE_NOTE,
        "last_batch": None if batch is None else {"id": batch.id, "imported_at": batch.imported_at.isoformat(), "rows": batch.row_count},
    }


def _looks_like_csv(payload: bytes | None) -> bool:
    if not payload or len(payload) < 200:
        return False
    head = payload[:240].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html") or head.startswith(b"{") :
        return False
    return b"," in payload[:800]


def _get(url: str, timeout: int = 90) -> tuple[bytes | None, dict]:
    request = Request(url, headers={"User-Agent": "AERP-research/0.4"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read()
            status = int(getattr(response, "status", 200) or 200)
            if not _looks_like_csv(payload):
                return None, {"status": "error", "http_status": status, "message": "响应不是 CSV", "url": url, "bytes": len(payload)}
            return payload, {"status": "success", "http_status": status, "url": url, "bytes": len(payload)}
    except HTTPError as exc:
        body = exc.read() if exc.fp else b""
        if _looks_like_csv(body):
            return body, {"status": "success", "http_status": exc.code, "url": url, "bytes": len(body), "note": "HTTP 错误码但正文是 CSV"}
        return None, {"status": "error", "http_status": exc.code, "message": str(exc), "url": url}
    except (URLError, TimeoutError, OSError) as exc:
        return None, {"status": "error", "message": str(exc), "url": url}


def fetch_who_csv(*, force_refresh: bool = False, cache_dir: Path | None = None) -> tuple[bytes | None, dict]:
    cache = Path(cache_dir or (ROOT / "data" / "raw"))
    cache.mkdir(parents=True, exist_ok=True)
    cache_file = cache / "who_covid19_global.csv"
    ttl = max(0, int(get_settings().aerp_who_cache_hours)) * 3600
    cache_ok = cache_file.exists() and cache_file.stat().st_size > 1000
    cache_age = (time.time() - cache_file.stat().st_mtime) if cache_ok else None
    if cache_ok and not force_refresh and cache_age is not None and cache_age < ttl and _looks_like_csv(cache_file.read_bytes()):
        payload = cache_file.read_bytes()
        return payload, {"status": "success", "source": "cache", "path": str(cache_file), "stale": False, "cache_age_seconds": cache_age, "vintage_kind": "采集时点快照"}
    attempts = []
    for url in WHO_CANDIDATE_URLS:
        payload, meta = _get(url)
        attempts.append(meta)
        if payload:
            cache_file.write_bytes(payload)
            meta.update({"source": "network", "stale": False, "sha256": sha256_bytes(payload), "vintage_kind": "采集时点快照", "attempts": attempts})
            return payload, meta
    if cache_ok and _looks_like_csv(cache_file.read_bytes()):
        payload = cache_file.read_bytes()
        return payload, {"status": "success", "source": "stale_cache", "stale": True, "path": str(cache_file), "cache_age_seconds": cache_age, "vintage_kind": "采集时点快照", "attempts": attempts}
    return None, {"status": "error", "attempts": attempts, "vintage_kind": "采集时点快照"}


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
            n_death_days=("new_deaths", lambda s: int(s.notna().sum())),
            country=("country", "last"),
        )
        .reset_index()
    )
    grouped["n_death_days"] = grouped.get("n_death_days") if "n_death_days" in grouped.columns else 0
    grouped["cases_quality"] = [
        "unreported" if n == 0 else "reported"
        for n in grouped["n_case_days"]
    ]
    grouped["cases_days_reported"] = grouped["n_case_days"]
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
        case_quality = getattr(item, "cases_quality", "reported")
        death_quality = "unreported" if int(getattr(item, "n_death_days", 0) or 0) == 0 else "reported"
        rows.append({**common, "metric": "weekly_cases", "value": None if pd.isna(item.new_cases) else float(item.new_cases), "unit": "cases", "quality_flag": case_quality})
        death_val = None if pd.isna(item.new_deaths) else float(item.new_deaths)
        rows.append({**{**common, "quality_flag": death_quality}, "metric": "weekly_deaths", "value": death_val, "unit": "deaths"})
    return pd.DataFrame(rows)


def weekly_from_official_week_table(frame: pd.DataFrame) -> pd.DataFrame | None:
    work = _normalize_columns(frame)
    week_col = next((c for c in work.columns if str(c).lower() in {"epiweek", "week", "epidemiological_week", "iso_week"}), None)
    end_col = next((c for c in work.columns if str(c).lower() in {"week_end", "period_end", "date_end"}), None)
    if week_col is None and end_col is None:
        return None
    if "country_code" not in work.columns or "new_cases" not in work.columns:
        return None
    work["country_code"] = work["country_code"].astype(str).str.strip().str.upper()
    work = work.loc[work["country_code"].str.len() == 2].copy()
    work["new_cases"] = pd.to_numeric(work["new_cases"], errors="coerce")
    work["new_deaths"] = pd.to_numeric(work.get("new_deaths"), errors="coerce") if "new_deaths" in work.columns else pd.NA
    if end_col:
        work["period_end"] = pd.to_datetime(work[end_col], errors="coerce").dt.normalize()
    else:
        return None
    work = work.dropna(subset=["period_end"])
    work["period_start"] = work["period_end"] - pd.Timedelta(days=6)
    work["n_days"] = 7
    work["n_case_days"] = work["new_cases"].notna().astype(int)
    work["n_death_days"] = work["new_deaths"].notna().astype(int) if "new_deaths" in work.columns else 0
    work["cases_quality"] = ["unreported" if n == 0 else "reported" for n in work["n_case_days"]]
    work["cases_days_reported"] = work["n_case_days"]
    keep = ["country_code", "period_start", "period_end", "new_cases", "new_deaths", "n_days", "n_case_days", "n_death_days", "country", "cases_quality", "cases_days_reported"]
    cols = [c for c in keep if c in work.columns]
    return work[cols].copy()


def parse_who_csv(payload: bytes, *, issued_at: datetime | None = None, max_weeks: int | None = 52) -> pd.DataFrame:
    head = payload[:200].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        raise ValueError("下载结果是 HTML 而不是 CSV。")
    text = payload.decode("utf-8-sig", errors="replace")
    frame = pd.read_csv(io.StringIO(text))
    weekly = weekly_from_official_week_table(frame)
    if weekly is None:
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


def fetch_and_import(db: Session, *, cache_dir: Path | None = None, max_weeks: int = 52, force_refresh: bool = False) -> dict:
    payload, meta = fetch_who_csv(force_refresh=force_refresh, cache_dir=cache_dir)
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
