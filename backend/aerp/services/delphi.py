from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aerp.config import ROOT
from aerp.core.hashing import canonical_hash, sha256_bytes, sha256_text
from aerp.core.timeutil import utcnow
from aerp.db.models import DataSource, Disease, ImportBatch, ObservationVintage, Region

EPIDATA_BASE = "https://api.delphi.cmu.edu/epidata"
NHSN_DOCS = "https://cmu-delphi.github.io/delphi-epidata/api/v5-signals/nhsn.html"
LICENSE_NOTE = (
    "Delphi Epidata 的 NHSN 住院信号来自 CDC NHSN，许可按 Delphi 与上游数据源条款。"
    "流感、COVID-19、RSV 使用各自独立信号，不能把某一病种连接器改名后宣称支持另一病种。"
    "本项目不改变第三方许可证。"
)
SIGNAL_BY_DISEASE = {
    "influenza": {
        "data_source": "nhsn",
        "signal": "confirmed_admissions_flu_ew",
        "metric": "hospital_admissions",
        "label": "周确诊流感住院",
    },
    "covid19": {
        "data_source": "nhsn",
        "signal": "confirmed_admissions_covid_ew",
        "metric": "hospital_admissions",
        "label": "周确诊 COVID-19 住院",
    },
    "rsv": {
        "data_source": "nhsn",
        "signal": "confirmed_admissions_rsv_ew",
        "metric": "hospital_admissions",
        "label": "周确诊 RSV 住院",
    },
}
RAW_DIR = ROOT / "data" / "raw" / "delphi"
VERIFICATION_PATH = RAW_DIR / "verification.json"

# 依次探测：V4 covidcast hhs 日信号、V4/V5 nhsn 周信号。成功一项即记录为已验证该路径。
PROBE_SPECS = (
    {
        "id": "covidcast_hhs_flu_day",
        "path": "/covidcast",
        "params": {
            "data_source": "hhs",
            "signal": "confirmed_admissions_influenza_1d",
            "time_type": "day",
            "geo_type": "state",
            "geo_value": "pa",
            "time_values": "20240101-20240114",
        },
        "note": "V4 covidcast：hhs/confirmed_admissions_influenza_1d",
    },
    {
        "id": "covidcast_nhsn_flu_week",
        "path": "/covidcast",
        "params": {
            "data_source": "nhsn",
            "signal": "confirmed_admissions_flu_ew",
            "time_type": "week",
            "geo_type": "state",
            "geo_value": "pa",
            "time_values": "202440",
        },
        "note": "covidcast：nhsn/confirmed_admissions_flu_ew（time_values 为 epiweek YYYYWW）",
    },
    {
        "id": "v5_nhsn_flu_week",
        "path": "/v5/covidcast",
        "params": {
            "data_source": "nhsn",
            "signal": "confirmed_admissions_flu_ew",
            "time_type": "week",
            "geo_type": "state",
            "geo_value": "pa",
            "time_values": "20240106",
        },
        "note": "V5 covidcast：nhsn/confirmed_admissions_flu_ew",
        "disease_id": "influenza",
    },
    {
        "id": "covidcast_nhsn_covid_week",
        "path": "/covidcast",
        "params": {
            "data_source": "nhsn",
            "signal": "confirmed_admissions_covid_ew",
            "time_type": "week",
            "geo_type": "state",
            "geo_value": "pa",
            "time_values": "202440",
        },
        "note": "covidcast：nhsn/confirmed_admissions_covid_ew（独立 COVID 信号）",
        "disease_id": "covid19",
    },
    {
        "id": "covidcast_nhsn_rsv_week",
        "path": "/covidcast",
        "params": {
            "data_source": "nhsn",
            "signal": "confirmed_admissions_rsv_ew",
            "time_type": "week",
            "geo_type": "state",
            "geo_value": "pa",
            "time_values": "202440",
        },
        "note": "covidcast：nhsn/confirmed_admissions_rsv_ew（独立 RSV 信号）",
        "disease_id": "rsv",
    },
)


def _get(url: str, timeout: int = 45) -> tuple[int, bytes]:
    request = Request(url, headers={"User-Agent": "aerp-research/0.2"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except HTTPError as exc:
        body = exc.read() if exc.fp else b""
        return int(exc.code), body
    except (URLError, TimeoutError, OSError) as exc:
        raise URLError(str(exc)) from exc


def _epidata_rows(payload: dict) -> list:
    if not isinstance(payload, dict):
        return []
    for key in ("epidata", "data", "result"):
        value = payload.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
    if payload.get("result") == 1 and isinstance(payload.get("epidata"), list):
        return payload["epidata"]
    return []


def _parse_delphi_time(series: pd.Series) -> pd.Series:
    """日信号为 YYYYMMDD；周信号常见 YYYYWW（epiweek），按 ISO 周六近似。MMWR 在年界可能差一周。"""
    text = series.astype(str).str.replace(r"\.0$", "", regex=True)
    out = pd.Series(pd.NaT, index=text.index, dtype="datetime64[ns]")
    yyyymmdd = text.str.fullmatch(r"\d{8}")
    yyyyww = text.str.fullmatch(r"\d{6}")
    if bool(yyyymmdd.any()):
        out.loc[yyyymmdd] = pd.to_datetime(text[yyyymmdd], format="%Y%m%d", errors="coerce")
    if bool(yyyyww.any()):
        years = text[yyyyww].str[:4].astype(int)
        weeks = text[yyyyww].str[4:].astype(int)
        stamps = []
        for year, week in zip(years.tolist(), weeks.tolist()):
            try:
                stamps.append(pd.to_datetime(f"{int(year)}-W{int(week):02d}-6", format="%G-W%V-%u"))
            except (ValueError, TypeError):
                stamps.append(pd.NaT)
        out.loc[yyyyww] = stamps
    rest = ~(yyyymmdd.fillna(False) | yyyyww.fillna(False))
    if bool(rest.any()):
        out.loc[rest] = pd.to_datetime(text[rest], errors="coerce")
    return out


def parse_epidata_json(payload: bytes | dict, *, source_id: str, disease_id: str | None = None) -> dict:
    data = json.loads(payload) if isinstance(payload, (bytes, bytearray)) else payload
    rows = _epidata_rows(data)
    if not rows:
        return {
            "status": "error",
            "message": f"Delphi 响应没有可用行：{str(data)[:240]}",
            "rows": pd.DataFrame(),
        }
    frame = pd.DataFrame(rows)
    columns = {item.lower(): item for item in frame.columns}
    geo = columns.get("geo_value") or columns.get("geo_id")
    time_col = columns.get("time_value") or columns.get("time_value_end") or columns.get("week")
    value_col = columns.get("value")
    issue_col = columns.get("issue") or columns.get("as_of")
    if geo is None or time_col is None or value_col is None:
        return {
            "status": "error",
            "message": f"无法识别 Delphi 列：{list(frame.columns)}",
            "rows": pd.DataFrame(),
        }
    period_end = _parse_delphi_time(frame[time_col])
    issued = period_end
    if issue_col:
        issued = _parse_delphi_time(frame[issue_col]).fillna(period_end)
    geo_value = frame[geo].astype(str)
    region_id = geo_value.where(~geo_value.str.fullmatch(r"\d+"), geo_value.str.zfill(2))
    inferred = disease_id
    if inferred is None:
        signal_name = str(frame[columns["signal"]].iloc[0]) if "signal" in columns else source_id
        if "covid" in str(signal_name).lower():
            inferred = "covid19"
        elif "rsv" in str(signal_name).lower():
            inferred = "rsv"
        else:
            inferred = "influenza"
    spec = SIGNAL_BY_DISEASE.get(inferred, SIGNAL_BY_DISEASE["influenza"])
    work = pd.DataFrame(
        {
            "disease_id": inferred,
            "region_id": region_id,
            "period_end": period_end,
            "issued_at": issued,
            "metric": spec["metric"],
            "value": pd.to_numeric(frame[value_col], errors="coerce"),
            "unit": "count",
            "source_id": source_id,
            "quality_flag": "observed",
        }
    )
    work["period_start"] = work["period_end"] - pd.Timedelta(days=6)
    work = work.dropna(subset=["period_end", "issued_at"])
    n_issues = int(work["issued_at"].nunique()) if not work.empty else 0
    if issue_col and n_issues > 1:
        replay_mode = "vintage"
        note = "同一周期有多个 issue 时，才按真实 vintage 回放。"
    else:
        replay_mode = "final_time_split"
        note = "当前 Delphi 响应只有单一 issue（常见为最新快照）。只能做最终数据时间切分，不能称为真实实时回测。"
    return {
        "status": "success",
        "message": f"已解析 Delphi {source_id}。",
        "rows": work,
        "n_rows": int(len(work)),
        "n_regions": int(work["region_id"].nunique()) if not work.empty else 0,
        "replay_mode": replay_mode,
        "n_distinct_issues": n_issues,
        "note": note,
    }


def verify_delphi(*, cache_dir: Path | None = None, payload_by_id: dict[str, bytes] | None = None) -> dict:
    """实际请求或用夹具验证 Delphi 路径。未成功时保持尚未验证，不伪造成功。"""
    cache_dir = cache_dir or RAW_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    probes = []
    verified = None
    for spec in PROBE_SPECS:
        url = EPIDATA_BASE + spec["path"] + "?" + urlencode(spec["params"])
        record = {
            "id": spec["id"],
            "url": url,
            "note": spec["note"],
            "docs": NHSN_DOCS,
        }
        try:
            if payload_by_id is not None:
                if spec["id"] not in payload_by_id:
                    record["verified"] = False
                    record["message"] = "本次探测未提供该路径的响应。"
                    probes.append(record)
                    continue
                status_code, body = 200, payload_by_id[spec["id"]]
            else:
                status_code, body = _get(url)
            record["http_status"] = status_code
            record["bytes"] = len(body)
            record["sha256"] = sha256_bytes(body)
            parsed_json = json.loads(body.decode("utf-8", errors="replace"))
            parsed = parse_epidata_json(parsed_json, source_id=spec["id"], disease_id=spec.get("disease_id"))
            record["parse_status"] = parsed.get("status")
            record["n_rows"] = parsed.get("n_rows", 0)
            record["message"] = parsed.get("message")
            if parsed.get("status") == "success" and int(parsed.get("n_rows") or 0) > 0:
                record["verified"] = True
                if verified is None:
                    verified = {**record, "parsed_preview": parsed["rows"].head(3).to_dict(orient="records")}
                    raw_path = cache_dir / f"{spec['id']}-{record['sha256'][:12]}.json"
                    raw_path.write_bytes(body)
            else:
                record["verified"] = False
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            record["verified"] = False
            record["message"] = str(exc)
        probes.append(record)
    result = {
        "status": "verified" if verified else "unverified",
        "connector_status": "available" if verified else "unverified",
        "verified_path": None if verified is None else verified.get("id"),
        "verified_url": None if verified is None else verified.get("url"),
        "probes": probes,
        "license_note": LICENSE_NOTE,
        "docs": NHSN_DOCS,
        "checked_at": utcnow().isoformat(sep=" ", timespec="seconds"),
        "input_hash": canonical_hash({"probes": [item.get("id") for item in probes], "verified": verified.get("id") if verified else None}),
        "note": (
            "已验证至少一条 Delphi 流感住院路径。"
            if verified
            else "全部探测失败或无数据行。Delphi Epidata 仍为尚未验证，不伪造成功。"
        ),
    }
    out = cache_dir / "verification.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if cache_dir.resolve() == RAW_DIR.resolve():
        VERIFICATION_PATH.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    return result


def last_verification() -> dict | None:
    if not VERIFICATION_PATH.exists():
        return None
    try:
        return json.loads(VERIFICATION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def connector_status() -> dict:
    last = last_verification()
    if last and last.get("status") == "verified":
        return {
            "status": "available",
            "status_label": "可用（已验证探测路径）",
            "note": f"已验证路径 {last.get('verified_path')}。许可见 Delphi 与 NHSN。失败时不伪造成功。",
            "url": last.get("verified_url") or NHSN_DOCS,
        }
    return {
        "status": "unverified",
        "status_label": "尚未验证",
        "note": "尚未验证。接入前需核查许可、覆盖范围、发布时间与运行条件。",
        "url": "https://github.com/cmu-delphi/delphi-epidata",
    }


def observations_from_delphi(parsed: dict) -> pd.DataFrame:
    if parsed.get("status") != "success":
        return pd.DataFrame()
    return parsed["rows"].copy()


def nhsn_time_window(start: str = "202240") -> str:
    iso = utcnow().isocalendar()
    return f"{start}-{iso.year}{iso.week:02d}"


def fetch_nhsn_weekly(
    disease_id: str,
    *,
    geo_value: str = "pa",
    time_values: str | None = None,
    cache_dir: Path | None = None,
    payload: bytes | None = None,
) -> dict:
    """按病种拉取 NHSN 周住院。COVID/RSV 使用独立信号，不是流感连接器改名。"""
    spec = SIGNAL_BY_DISEASE.get(disease_id)
    if spec is None:
        return {"status": "error", "message": f"没有 {disease_id} 的 NHSN 周住院信号配置。", "rows": pd.DataFrame()}
    time_values = time_values or nhsn_time_window()
    cache_dir = cache_dir or RAW_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    params = {
        "data_source": spec["data_source"],
        "signal": spec["signal"],
        "time_type": "week",
        "geo_type": "state",
        "geo_value": geo_value,
        "time_values": time_values,
    }
    url = EPIDATA_BASE + "/covidcast?" + urlencode(params)
    errors = []
    if payload is None:
        try:
            status_code, body = _get(url)
        except (URLError, TimeoutError, OSError) as exc:
            return {
                "status": "error",
                "message": f"Delphi {disease_id}/{spec['signal']} 请求失败：{exc}",
                "source_url": url,
                "rows": pd.DataFrame(),
            }
        if status_code >= 400:
            return {
                "status": "error",
                "message": f"Delphi {disease_id}/{spec['signal']} HTTP {status_code}：{body[:240]!r}",
                "source_url": url,
                "rows": pd.DataFrame(),
            }
        payload = body
    checksum = sha256_bytes(payload)
    raw_path = cache_dir / f"nhsn-{disease_id}-{checksum[:12]}.json"
    if not raw_path.exists():
        raw_path.write_bytes(payload)
    parsed = parse_epidata_json(payload, source_id=f"nhsn_{disease_id}", disease_id=disease_id)
    parsed["provenance"] = {
        "source_url": url,
        "downloaded_at": utcnow().isoformat(sep=" ", timespec="seconds"),
        "sha256": checksum,
        "raw_path": str(raw_path),
        "signal": spec["signal"],
        "disease_id": disease_id,
        "license_note": LICENSE_NOTE,
        "note": "downloaded_at 不是历史发布时间。无 issue 时不能称为真实 vintage。",
        "fetch_notes": errors,
    }
    return parsed


def import_nhsn_observations(db: Session, *, disease_id: str = "influenza", geo_value: str = "*", time_values: str | None = None) -> dict:
    fetched = fetch_nhsn_weekly(disease_id, geo_value=geo_value, time_values=time_values)
    frame = fetched.get("rows")
    if fetched.get("status") != "success" or frame is None or getattr(frame, "empty", True):
        return {
            "status": "error",
            "message": fetched.get("message") or "NHSN 周住院拉取失败或无行。夹具通过不能代替真实验收。",
            "fetch": {k: v for k, v in fetched.items() if k != "rows"},
            "is_fixture": False,
        }
    source_id = f"nhsn_{disease_id}"
    max_end = db.scalar(
        select(func.max(ObservationVintage.period_end)).where(
            ObservationVintage.disease_id == disease_id,
            ObservationVintage.source_id == source_id,
        )
    )
    if max_end is not None:
        frame = frame.loc[pd.to_datetime(frame["period_end"]) > pd.Timestamp(max_end)].copy()
        if frame.empty:
            return {
                "status": "up_to_date",
                "message": "已有 NHSN 周住院，没有比本地更新的周期。",
                "max_period_end": str(pd.Timestamp(max_end).date()),
                "is_fixture": False,
                "provenance": fetched.get("provenance"),
            }
    if db.get(Disease, disease_id) is None:
        db.add(Disease(id=disease_id, name=disease_id, description="NHSN 住院信号独立病种。", is_synthetic=False))
        db.flush()
    source = db.get(DataSource, source_id)
    if source is None:
        db.add(
            DataSource(
                id=source_id,
                name=f"Delphi NHSN {disease_id} weekly admissions",
                url=NHSN_DOCS,
                license=LICENSE_NOTE,
                update_note="采集时点快照。无 issue 列时不能称为官方历史版本。",
                connector_status="available",
            )
        )
        db.flush()
    for region_id in frame["region_id"].astype(str).unique():
        if db.get(Region, region_id) is None:
            db.add(Region(id=region_id, name=region_id.upper(), note="USPS state / Delphi geo_value", scenario="nhsn"))
    payload_hash = sha256_text(frame.to_json(orient="records", date_format="iso"))
    batch_id = f"nhsn-{disease_id}-{payload_hash[:12]}"
    existing = db.get(ImportBatch, batch_id)
    if existing:
        return {
            "status": "exists",
            "batch_id": batch_id,
            "n_rows": existing.row_count,
            "is_fixture": False,
            "vintage_kind": "采集时点快照",
            "provenance": fetched.get("provenance"),
        }
    db.add(
        ImportBatch(
            id=batch_id,
            source_id=source_id,
            file_hash=payload_hash,
            filename=f"nhsn-{disease_id}.json",
            imported_at=utcnow(),
            row_count=int(len(frame)),
            note=LICENSE_NOTE,
            is_synthetic=False,
        )
    )
    db.flush()
    existing_keys = {
        (str(row.region_id), pd.Timestamp(row.period_start), pd.Timestamp(row.issued_at))
        for row in db.execute(
            select(ObservationVintage.region_id, ObservationVintage.period_start, ObservationVintage.issued_at).where(
                ObservationVintage.disease_id == disease_id,
                ObservationVintage.source_id == source_id,
            )
        )
    }
    n = 0
    for row in frame.itertuples(index=False):
        key = (str(row.region_id), pd.Timestamp(row.period_start), pd.Timestamp(row.issued_at))
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
                source_id=source_id,
                quality_flag=str(row.quality_flag),
                import_batch_id=batch_id,
            )
        )
        existing_keys.add(key)
        n += 1
    if n == 0:
        db.rollback()
        return {
            "status": "up_to_date",
            "message": "拉取到的 NHSN 行与本地主键重复，没有写入新周。",
            "is_fixture": False,
            "provenance": fetched.get("provenance"),
        }
    db.commit()
    return {
        "status": "success",
        "batch_id": batch_id,
        "n_rows": n,
        "n_regions": int(frame["region_id"].nunique()),
        "is_fixture": False,
        "is_synthetic": False,
        "vintage_kind": "采集时点快照",
        "provenance": fetched.get("provenance"),
        "metrics": sorted(set(frame["metric"].astype(str))),
    }


def verified_diseases(last: dict | None = None) -> list[str]:
    last = last if last is not None else last_verification()
    if not last:
        return []
    found = []
    for probe in last.get("probes") or []:
        if probe.get("verified") and probe.get("id"):
            if "covid" in probe["id"] and "covid19" not in found:
                found.append("covid19")
            elif "rsv" in probe["id"] and "rsv" not in found:
                found.append("rsv")
            elif "flu" in probe["id"] and "influenza" not in found:
                found.append("influenza")
    return found
