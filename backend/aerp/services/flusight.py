from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import pandas as pd

from aerp.config import ROOT
from aerp.core.hashing import canonical_hash, sha256_bytes
from aerp.core.timeutil import utcnow

FLUSIGHT_REPO = "https://github.com/cdcepi/FluSight-forecast-hub"
SNAPSHOT_URL = "https://raw.githubusercontent.com/cdcepi/FluSight-forecast-hub/main/target-data/target-hospital-admissions.csv"
VINTAGE_URL = "https://raw.githubusercontent.com/cdcepi/FluSight-forecast-hub/main/target-data/time-series.csv"
VINTAGE_URLS = (
    "https://media.githubusercontent.com/media/cdcepi/FluSight-forecast-hub/main/target-data/time-series.csv",
    VINTAGE_URL,
)
NHSN_PAGE = "https://data.cdc.gov/Public-Health-Surveillance/Weekly-Hospital-Respiratory-Data-HRD-Metrics-by-Ju/ua7e-t2fy/about_data"
LICENSE_NOTE = (
    "FluSight 目标数据来自 CDC NHSN 周住院呼吸道数据集，经 FluSight Forecast Hub 整理。"
    "用于研究与发表前请按 Hub README 联系 flusight@cdc.gov 了解归属要求。"
    "本项目不改变第三方数据许可证。"
)
AS_OF_NOTE = (
    "time-series.csv 的 as_of 在 2025-07-05 之前表示数据发布对应的 reference_date；"
    "之后表示数据发布日（通常为 reference_date 后的周三）。"
    "这是 Hub 记录的发布时间，不是本机下载时间，也不是随意文件修改时间。"
)
AS_OF_BREAK = pd.Timestamp("2025-07-05")
DEFAULT_TARGET = "wk inc flu hosp"
DEFAULT_RESEARCH_LOCATIONS = ("US", "06", "36", "48", "42")
RAW_DIR = ROOT / "data" / "raw" / "flusight"


def _download(url: str, timeout: int = 300) -> bytes:
    request = Request(url, headers={"User-Agent": "aerp-research/0.2"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _looks_like_lfs_pointer(payload: bytes) -> bool:
    head = payload[:200].decode("utf-8", errors="ignore")
    return head.startswith("version https://git-lfs.github.com") or "oid sha256:" in head[:400]


def vintage_issued_at(as_of: pd.Series) -> tuple[pd.Series, list[str]]:
    """按 Hub 在 2025-07-05 前后的 as_of 语义转换成 issued_at。"""
    stamps = pd.to_datetime(as_of)
    issued = stamps.copy()
    pre = stamps < AS_OF_BREAK
    saturday = stamps.dt.dayofweek == 5
    convert = pre & saturday
    issued.loc[convert] = stamps.loc[convert] - pd.Timedelta(days=3)
    logs = [
        AS_OF_NOTE,
        f"2025-07-05 之前且为周六的 as_of {int(convert.sum())} 条按 reference_date 转为周三发布日（减 3 天）。",
        f"2025-07-05 之前但不是周六的 as_of {int((pre & ~saturday).sum())} 条保持原值，不臆造周几。",
        f"2025-07-05 及之后 {int((~pre).sum())} 条按发布日使用 as_of。",
    ]
    return issued, logs


def subset_observations(
    rows: pd.DataFrame,
    *,
    locations: list[str] | tuple[str, ...] | None = None,
    start=None,
    end=None,
) -> pd.DataFrame:
    work = rows.copy()
    if locations:
        keep = {str(item).zfill(2) if str(item).isdigit() else str(item) for item in locations}
        work = work[work["region_id"].astype(str).isin(keep)]
    if start is not None:
        work = work[pd.to_datetime(work["period_end"]) >= pd.to_datetime(start)]
    if end is not None:
        work = work[pd.to_datetime(work["period_end"]) <= pd.to_datetime(end)]
    return work.reset_index(drop=True)


def flu_season_origins(
    period_ends,
    *,
    min_train_weeks: int = 26,
    step_weeks: int = 2,
    skip_months: tuple[int, ...] = (6, 7, 8, 9),
) -> list[pd.Timestamp]:
    """FluSight 回放原点：周六周结束后的周三（数据通常在该日发布）。"""
    weeks = sorted(pd.to_datetime(pd.Index(period_ends)).unique())
    origins: list[pd.Timestamp] = []
    end = len(weeks) - 4
    if end <= int(min_train_weeks):
        end = len(weeks)
    for index in range(int(min_train_weeks), end, int(step_weeks)):
        saturday = pd.Timestamp(weeks[index]).normalize()
        if int(saturday.month) in skip_months:
            continue
        origins.append(saturday + pd.Timedelta(days=4))
    return origins


def _reuse_cached(kind: str, cache_dir: Path) -> bytes | None:
    files = sorted(cache_dir.glob(f"{kind}-*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files:
        if path.stat().st_size > 10_000:
            return path.read_bytes()
    return None


def fetch_flusight_target(
    *,
    kind: str = "snapshot",
    cache_dir: Path | None = None,
    url: str | None = None,
    payload: bytes | None = None,
    locations: list[str] | tuple[str, ...] | None = None,
    target: str | None = DEFAULT_TARGET,
    reuse_cache: bool = True,
) -> dict:
    """下载或读取 FluSight 住院目标。snapshot 无 as_of，只能做最终数据时间切分。"""
    cache_dir = cache_dir or RAW_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    if kind == "snapshot":
        source_urls = [url] if url else [SNAPSHOT_URL]
        replay_mode = "final_time_split"
        timeout = 60
    elif kind == "vintage":
        source_urls = [url] if url else list(VINTAGE_URLS)
        replay_mode = "vintage"
        timeout = 600
    else:
        raise ValueError("kind 只能是 snapshot 或 vintage")
    downloaded_at = utcnow()
    source_url = source_urls[0]
    errors: list[str] = []
    if payload is None:
        for candidate in source_urls:
            source_url = candidate
            try:
                payload = _download(candidate, timeout=timeout)
                if _looks_like_lfs_pointer(payload):
                    errors.append(f"{candidate} 返回 Git LFS 指针，不是数据文件。")
                    payload = None
                    continue
                if kind == "vintage" and len(payload) < 1_000_000:
                    errors.append(f"{candidate} 仅 {len(payload)} 字节，不像完整 time-series.csv。")
                    payload = None
                    continue
                break
            except (URLError, TimeoutError, OSError, ValueError) as exc:
                errors.append(f"{candidate}: {exc}")
                payload = None
        if payload is None and reuse_cache:
            cached = _reuse_cached(kind, cache_dir)
            if cached is not None:
                payload = cached
                source_url = f"cache:{cache_dir}"
                errors.append("网络获取失败，改用本地缓存。缓存不是新的独立发布时间。")
        if payload is None:
            return {
                "status": "error",
                "message": f"无法获取 FluSight 数据：{' | '.join(errors) or '未知错误'}。接口可用，但不伪造成功。",
                "source_url": source_url,
                "replay_mode": replay_mode,
                "license_note": LICENSE_NOTE,
            }
    checksum = sha256_bytes(payload)
    raw_path = cache_dir / f"{kind}-{checksum[:12]}.csv"
    if not raw_path.exists():
        raw_path.write_bytes(payload)
    provenance = {
        "source_url": source_url,
        "hub": FLUSIGHT_REPO,
        "nhsn": NHSN_PAGE,
        "downloaded_at": downloaded_at.isoformat(sep=" ", timespec="seconds"),
        "sha256": checksum,
        "bytes": len(payload),
        "kind": kind,
        "replay_mode": replay_mode,
        "as_of_semantics": AS_OF_NOTE if kind == "vintage" else "snapshot 文件没有 as_of，不能称为真实发布时间回放。",
        "license_note": LICENSE_NOTE,
        "raw_path": str(raw_path),
        "fetch_notes": errors,
    }
    (cache_dir / f"{kind}-{checksum[:12]}.provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    parsed = parse_flusight_csv(
        payload,
        kind=kind,
        downloaded_at=downloaded_at,
        locations=locations,
        target=target,
    )
    parsed["provenance"] = provenance
    parsed["input_hash"] = canonical_hash({"sha256": checksum, "kind": kind, "locations": list(locations or []), "target": target})
    return parsed


def parse_flusight_csv(
    payload: bytes,
    *,
    kind: str,
    downloaded_at=None,
    locations: list[str] | tuple[str, ...] | None = None,
    target: str | None = DEFAULT_TARGET,
) -> dict:
    frame = pd.read_csv(BytesIO(payload))
    columns = {item.lower(): item for item in frame.columns}
    logs = []
    if "target" in columns and target:
        before = len(frame)
        frame = frame[frame[columns["target"]].astype(str).str.strip() == target]
        logs.append(f"按 target={target} 过滤，保留 {len(frame)}/{before} 行。")
    if kind == "vintage":
        if "as_of" not in columns:
            return {
                "status": "error",
                "message": "vintage 文件缺少 as_of，拒绝把下载时间伪装成真实发布时间。",
                "rows": pd.DataFrame(),
                "logs": ["missing as_of"],
            }
        as_of_raw = pd.to_datetime(frame[columns["as_of"]])
        issued, as_of_logs = vintage_issued_at(as_of_raw)
        logs.append("issued_at 由 Hub 的 as_of 按 2025-07-05 语义转换，不是下载时间。")
        logs.extend(as_of_logs)
    else:
        as_of_raw = None
        issued = None
        logs.append("snapshot 无发布时间，issued_at 记为 period_end，replay_mode 必须为 final_time_split。")

    date_col = columns.get("date") or columns.get("target_end_date")
    loc_col = columns.get("location")
    name_col = columns.get("location_name")
    value_col = columns.get("value") or columns.get("observation")
    if date_col is None or loc_col is None or value_col is None:
        return {
            "status": "error",
            "message": f"无法识别 FluSight 列：{list(frame.columns)}",
            "rows": pd.DataFrame(),
            "logs": logs,
        }
    region_id = frame[loc_col].astype(str)
    region_id = region_id.where(~region_id.str.fullmatch(r"\d+"), region_id.str.zfill(2))
    work = pd.DataFrame(
        {
            "disease_id": "influenza",
            "region_id": region_id,
            "region_name": frame[name_col].astype(str) if name_col else region_id,
            "period_end": pd.to_datetime(frame[date_col]),
            "metric": "hospital_admissions",
            "value": pd.to_numeric(frame[value_col], errors="coerce"),
            "unit": "count",
            "source_id": "flusight",
            "quality_flag": "observed",
        }
    )
    work["period_start"] = work["period_end"] - pd.Timedelta(days=6)
    if as_of_raw is not None:
        work["as_of_raw"] = as_of_raw
    if issued is None:
        work["issued_at"] = work["period_end"]
        replay_mode = "final_time_split"
    else:
        work["issued_at"] = issued
        replay_mode = "vintage"
    missing = int(work["value"].isna().sum())
    if missing:
        work.loc[work["value"].isna(), "quality_flag"] = "missing"
        logs.append(f"缺失 {missing} 行，保持为空，未记为 0。")
    before = len(work)
    work = work.drop_duplicates(["region_id", "period_end", "issued_at"], keep="last")
    if len(work) < before:
        logs.append(f"删除 {before - len(work)} 条重复。")
    if locations:
        work = subset_observations(work, locations=locations)
        logs.append(f"地区子集 {list(locations)}，剩余 {len(work)} 行。")
    dictionary = {
        "disease_id": "influenza",
        "metric": "hospital_admissions",
        "unit": "count",
        "period": "epidemiological week Sunday-Saturday; period_end is Saturday",
        "region_id": "US FIPS location code from FluSight locations",
        "value": "weekly confirmed influenza hospital admissions (NHSN totalconfflunewadm)",
        "as_of": AS_OF_NOTE if kind == "vintage" else "snapshot 无 as_of",
    }
    n_as_of_break = 0
    if "as_of_raw" in work.columns and not work.empty:
        n_as_of_break = int((pd.to_datetime(work["as_of_raw"]) >= AS_OF_BREAK).sum())
    return {
        "status": "success",
        "message": "已解析 FluSight 住院目标。",
        "rows": work,
        "n_rows": int(len(work)),
        "n_regions": int(work["region_id"].nunique()) if not work.empty else 0,
        "replay_mode": replay_mode,
        "logs": logs,
        "dictionary": dictionary,
        "n_as_of_after_break": n_as_of_break,
        "downloaded_at": None if downloaded_at is None else pd.Timestamp(downloaded_at).isoformat(),
    }


def observations_from_flusight(parsed: dict) -> pd.DataFrame:
    if parsed.get("status") != "success":
        return pd.DataFrame()
    return parsed["rows"].copy()


def import_flusight_map_observations(db, *, kind: str = "snapshot") -> dict:
    from aerp.core.hashing import sha256_text
    from aerp.db.models import DataSource, Disease, ImportBatch, ObservationVintage, Region

    parsed = fetch_flusight_target(kind=kind)
    frame = parsed.get("rows")
    if parsed.get("status") != "success" or frame is None or getattr(frame, "empty", True):
        return {
            "status": "error",
            "message": parsed.get("message") or "FluSight 目标文件不可用。",
            "fetch": {k: v for k, v in parsed.items() if k != "rows"},
            "is_fixture": False,
        }
    if db.get(Disease, "influenza") is None:
        db.add(Disease(id="influenza", name="流感", description="FluSight / NHSN 住院。", is_synthetic=False))
        db.flush()
    if db.get(DataSource, "flusight") is None:
        db.add(
            DataSource(
                id="flusight",
                name="CDC FluSight target hospital admissions",
                url=FLUSIGHT_REPO,
                license=LICENSE_NOTE,
                update_note=LICENSE_NOTE,
                connector_status="available",
            )
        )
        db.flush()
    for region_id in frame["region_id"].astype(str).unique():
        if db.get(Region, region_id) is None:
            db.add(Region(id=region_id, name=region_id, note="FluSight location / FIPS", scenario="flusight"))
    payload_hash = sha256_text(frame.to_json(orient="records", date_format="iso"))
    batch_id = f"flusight-map-{payload_hash[:12]}"
    existing = db.get(ImportBatch, batch_id)
    if existing:
        return {
            "status": "exists",
            "batch_id": batch_id,
            "n_rows": existing.row_count,
            "is_fixture": False,
            "replay_mode": parsed.get("replay_mode"),
            "provenance": parsed.get("provenance"),
        }
    db.add(
        ImportBatch(
            id=batch_id,
            source_id="flusight",
            file_hash=payload_hash,
            filename=f"flusight-{kind}.csv",
            imported_at=utcnow(),
            row_count=int(len(frame)),
            note=LICENSE_NOTE,
            is_synthetic=False,
        )
    )
    db.flush()
    n = 0
    for row in frame.itertuples(index=False):
        db.add(
            ObservationVintage(
                disease_id="influenza",
                region_id=str(row.region_id),
                period_start=pd.Timestamp(row.period_start).to_pydatetime(),
                period_end=pd.Timestamp(row.period_end).to_pydatetime(),
                issued_at=pd.Timestamp(row.issued_at).to_pydatetime(),
                metric="hospital_admissions",
                value=None if pd.isna(row.value) else float(row.value),
                unit="count",
                source_id="flusight",
                quality_flag=str(row.quality_flag),
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
        "is_fixture": False,
        "replay_mode": parsed.get("replay_mode"),
        "vintage_kind": "Hub snapshot" if kind == "snapshot" else "Hub as_of vintage",
        "provenance": parsed.get("provenance"),
    }
