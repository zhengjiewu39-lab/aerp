from __future__ import annotations

import json
from pathlib import Path

from aerp.core.hashing import canonical_hash, sha256_bytes
from aerp.core.timeutil import utcnow
from aerp.research.runs import RUNS_DIR, write_json

ARCHIVE_NOTE = (
    "本地归档保存生成时间、数据截止时间和预测哈希。本地时间戳、本机 hash chain 和 Git 提交"
    "本身不能证明独立前瞻公开发布。如需外部时间戳，应使用预先登记的公共仓储或 RFC 3161 / OpenTimestamps，"
    "且未经授权不得上传。"
)

EXTERNAL_PROTOCOL = {
    "status": "local_only",
    "accepted_proofs": [
        "RFC 3161 时间戳令牌（针对本文件 sha256）",
        "OpenTimestamps .ots 证明",
        "预先登记的公共预测 Hub 提交回执（如 FluSight），含公开 commit 或对象哈希",
        "独立第三方公证的哈希清单",
    ],
    "not_accepted_as_public_release": [
        "本机 generated_at",
        "本机 hash chain 单独存在",
        "未推送的 git commit",
        "文件修改时间",
        "下载 FluSight 目标数据的时间",
    ],
    "instruction": (
        "生成 stamp_request.json 后，把 sha256 交给外部时间戳服务或公共仓储。"
        "得到证明文件后用 archive-attach 附加。未附加前 status 保持 local_only。"
    ),
}


def _chain_path(archive_dir: Path) -> Path:
    return archive_dir / "chain.jsonl"


def _last_chain_hash(archive_dir: Path) -> str | None:
    path = _chain_path(archive_dir)
    if not path.exists():
        return None
    last = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            last = json.loads(line)
    return None if last is None else last.get("record_hash")


def archive_forecast(payload: dict, *, archive_dir: Path | None = None) -> dict:
    archive_dir = archive_dir or (RUNS_DIR / "prospective")
    archive_dir.mkdir(parents=True, exist_ok=True)
    previous = _last_chain_hash(archive_dir)
    record = {
        "generated_at": utcnow().isoformat(sep=" ", timespec="seconds"),
        "cutoff_time": payload.get("cutoff_time"),
        "target_week_ends": payload.get("target_week_ends"),
        "frozen_config": payload.get("config") or {},
        "forecasts": payload.get("forecasts") or [],
        "previous_hash": previous,
        "hash": canonical_hash(payload),
        "note": ARCHIVE_NOTE,
        "external_timestamp": {"status": "local_only", "protocol": EXTERNAL_PROTOCOL},
    }
    record["record_hash"] = canonical_hash({k: record[k] for k in record if k != "record_hash"})
    name = f"{record['generated_at'].replace(' ', 'T').replace(':', '')}-{record['hash'][:10]}.json"
    path = archive_dir / name
    if path.exists():
        return {"status": "exists", "path": str(path), "hash": record["hash"], "note": "追加保存，不覆盖。"}
    write_json(archive_dir, name, record)
    stamp = {
        "file": name,
        "sha256": sha256_bytes(path.read_bytes()),
        "record_hash": record["record_hash"],
        "previous_hash": previous,
        "generated_at_local": record["generated_at"],
        "status": "local_only",
        "protocol": EXTERNAL_PROTOCOL,
    }
    write_json(archive_dir, f"{path.stem}.stamp_request.json", stamp)
    with _chain_path(archive_dir).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"file": name, "record_hash": record["record_hash"], "previous_hash": previous}, ensure_ascii=False) + "\n")
    return {
        "status": "saved",
        "path": str(path),
        "hash": record["hash"],
        "record_hash": record["record_hash"],
        "stamp_request": str(archive_dir / f"{path.stem}.stamp_request.json"),
        "external_timestamp_status": "local_only",
        "note": ARCHIVE_NOTE,
    }


def verify_chain(archive_dir: Path | None = None) -> dict:
    archive_dir = archive_dir or (RUNS_DIR / "prospective")
    path = _chain_path(archive_dir)
    if not path.exists():
        return {"status": "empty", "note": "尚无前瞻归档。"}
    previous = None
    items = []
    ok = True
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("previous_hash") != previous:
            ok = False
            items.append({**row, "chain_ok": False})
        else:
            items.append({**row, "chain_ok": True})
        previous = row.get("record_hash")
    return {
        "status": "ok" if ok else "broken",
        "n": len(items),
        "items": items,
        "note": "链完整只说明本地未断档，不能证明独立公开发布。",
    }


def attach_external_proof(stamp_request: Path, proof: Path) -> dict:
    request = json.loads(Path(stamp_request).read_text(encoding="utf-8"))
    payload = Path(proof).read_bytes()
    attached = {
        **request,
        "status": "external_proof_attached",
        "proof_path": str(proof),
        "proof_sha256": sha256_bytes(payload),
        "attached_at": utcnow().isoformat(sep=" ", timespec="seconds"),
        "note": "已附加外部证明文件。是否构成独立公开发布仍取决于证明机构与预先登记，本函数不做法律认定。",
    }
    out = Path(stamp_request).with_name(Path(stamp_request).stem.replace(".stamp_request", "") + ".stamp_attached.json")
    if out.name == Path(stamp_request).name:
        out = Path(stamp_request).with_name(Path(stamp_request).stem + ".attached.json")
    out.write_text(json.dumps(attached, ensure_ascii=False, indent=2), encoding="utf-8")
    return attached
