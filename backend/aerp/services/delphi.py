from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from aerp.config import ROOT
from aerp.core.hashing import canonical_hash, sha256_bytes
from aerp.core.timeutil import utcnow

EPIDATA_BASE = "https://api.delphi.cmu.edu/epidata"
NHSN_DOCS = "https://cmu-delphi.github.io/delphi-epidata/api/v5-signals/nhsn.html"
LICENSE_NOTE = (
    "Delphi Epidata 的 NHSN 流感住院信号来自 CDC NHSN，许可按 Delphi 与上游数据源条款。"
    "本项目不改变第三方许可证。"
)
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


def parse_epidata_json(payload: bytes | dict, *, source_id: str) -> dict:
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
    work = pd.DataFrame(
        {
            "disease_id": "influenza",
            "region_id": region_id,
            "period_end": period_end,
            "issued_at": issued,
            "metric": "hospital_admissions",
            "value": pd.to_numeric(frame[value_col], errors="coerce"),
            "unit": "count",
            "source_id": source_id,
            "quality_flag": "observed",
        }
    )
    work["period_start"] = work["period_end"] - pd.Timedelta(days=6)
    work = work.dropna(subset=["period_end", "issued_at"])
    return {
        "status": "success",
        "message": f"已解析 Delphi {source_id}。",
        "rows": work,
        "n_rows": int(len(work)),
        "n_regions": int(work["region_id"].nunique()) if not work.empty else 0,
        "replay_mode": "vintage" if issue_col else "issue_unknown",
        "note": "无 issue/as_of 时不能称为真实 vintage。",
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
            parsed = parse_epidata_json(parsed_json, source_id=spec["id"])
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
