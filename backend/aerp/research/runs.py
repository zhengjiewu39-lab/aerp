from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from aerp import __version__ as AERP_VERSION
from aerp.config import ROOT
from aerp.core.hashing import canonical_hash, git_commit_hash, python_runtime, sha256_text
from aerp.core.timeutil import utcnow
from aerp.services.experiment_engine import dependency_versions

RUNS_DIR = ROOT / "research_runs"


def new_run_id() -> str:
    stamp = utcnow().strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{uuid4().hex[:8]}"


def start_run(config: dict, *, input_hash: str, seed: int) -> dict:
    run_id = new_run_id()
    directory = RUNS_DIR / run_id
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "run_id": run_id,
        "created_at": utcnow().isoformat(sep=" ", timespec="seconds"),
        "config": config,
        "config_hash": canonical_hash(config),
        "input_hash": input_hash,
        "git_commit": git_commit_hash(),
        "code_version": AERP_VERSION,
        "dependency_versions": dependency_versions(),
        "python": python_runtime(),
        "random_seed": int(seed),
        "directory": str(directory),
    }
    (directory / "run.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def write_json(run_dir: Path, name: str, payload: dict | list) -> Path:
    path = Path(run_dir) / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def write_text(run_dir: Path, name: str, text: str) -> Path:
    path = Path(run_dir) / name
    path.write_text(text, encoding="utf-8")
    return path


def incomplete_artifacts(reason: str) -> dict:
    return {
        "status": "incomplete",
        "message": reason,
        "scores": None,
        "significance": None,
        "paper_conclusion": None,
        "note": "实验未运行或无数据时不生成虚构成绩、显著性或论文结论。",
    }


def snapshot_hash(path: Path) -> str:
    return sha256_text(path.read_text(encoding="utf-8"))
