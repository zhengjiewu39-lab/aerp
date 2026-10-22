from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from aerp.config import ROOT


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256_text(raw)


def hash_observations(frame: pd.DataFrame) -> str:
    columns = [
        "disease_id",
        "region_id",
        "metric",
        "period_start",
        "period_end",
        "issued_at",
        "value",
        "unit",
        "source_id",
        "quality_flag",
    ]
    if frame.empty:
        return sha256_text("[]")
    slim = frame.loc[:, [col for col in columns if col in frame.columns]].copy()
    for col in ("period_start", "period_end", "issued_at"):
        if col in slim.columns:
            slim[col] = pd.to_datetime(slim[col]).dt.strftime("%Y-%m-%dT%H:%M:%S")
    slim = slim.sort_values(list(slim.columns)).reset_index(drop=True)
    return sha256_text(slim.to_json(orient="records", date_format="iso"))


def git_commit_hash(cwd: Path | None = None) -> str:
    root = cwd or ROOT
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def python_runtime() -> dict[str, str]:
    return {"python": sys.version.split()[0], "executable": sys.executable}
