"""登革热适配接口与来源核查。不伪造周病例。"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from aerp.config import ROOT
from aerp.core.hashing import sha256_bytes
from aerp.core.timeutil import utcnow

CANDIDATE_SOURCES = (
    {
        "id": "paho_plisa",
        "name": "PAHO PLISA dengue indicators",
        "url": "https://www3.paho.org/data/index.php/en/mnu-topics/indicadores-dengue-en.html",
        "license": "PAHO 条款，导入前需核对再分发条件。",
        "expected_fields": ["country", "week", "probable_cases", "confirmed_cases"],
    },
    {
        "id": "who_dengue",
        "name": "WHO dengue / outbreak pages",
        "url": "https://www.who.int/health-topics/dengue-and-severe-dengue",
        "license": "WHO 公开页面，通常不是完整 vintage 时序。",
        "expected_fields": ["event narrative"],
    },
)
RAW_DIR = ROOT / "data" / "raw" / "dengue"
STATUS_PATH = RAW_DIR / "source_check.json"


def _probe(url: str, timeout: int = 20) -> dict:
    request = Request(url, headers={"User-Agent": "aerp-research/0.3"}, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(8000)
            return {
                "ok": int(response.status) < 400,
                "http_status": int(response.status),
                "bytes_preview": len(body),
                "sha256_preview": sha256_bytes(body),
                "message": "页面可访问，但尚未解析为周病例时序。",
            }
    except HTTPError as exc:
        return {"ok": False, "http_status": int(exc.code), "message": f"HTTP {exc.code}"}
    except (URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "http_status": None, "message": str(exc)}


def check_dengue_sources(*, live: bool = False) -> dict:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    probes = []
    for item in CANDIDATE_SOURCES:
        record = dict(item)
        if live:
            record["probe"] = _probe(item["url"])
        else:
            record["probe"] = {
                "ok": None,
                "message": "默认离线核查：只记录候选来源，不把下载时间当作历史发布时间。",
            }
        probes.append(record)
    result = {
        "status": "pending",
        "trainable": False,
        "replay_mode_allowed": None,
        "message": "登革热适配接口已就绪。尚未确认可再分发的周病例修订版本，因此不接入真实训练，也不伪造病例。",
        "sources": probes,
        "checked_at": utcnow().isoformat(sep=" ", timespec="seconds"),
        "note": "有可靠数据后再做真实接入。无历史版本时最多做最终数据时间切分。",
    }
    STATUS_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def adapter_status() -> dict:
    if STATUS_PATH.exists():
        try:
            return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "status": "pending",
        "trainable": False,
        "message": "尚未完成登革热来源核查文件。运行 python -m aerp.research check-dengue。",
    }
