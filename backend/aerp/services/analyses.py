"""Evidence packs, analysis records, and backend-enforced review transitions."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aerp.config import get_settings
from aerp.core.hashing import canonical_hash
from aerp.core.timeutil import utcnow
from aerp.db.models import AnalysisRecord, AnalysisRevision, EvidenceSnapshot, ReviewDecision
from aerp.services import ai_provider
from aerp.services.map_data import region_detail

IDENTITY_NOTE = ai_provider.IDENTITY_NOTE
REQUIRED_FIELDS = (
    "summary",
    "observations",
    "possible_explanations",
    "uncertainties",
    "data_quality_issues",
    "suggested_checks",
    "citations",
)
CONFIRMABLE = {"pending_review"}
TERMINAL = {"confirmed", "rejected"}


def _analysis_id() -> str:
    return "an_" + uuid.uuid4().hex[:16]


def _snapshot_id() -> str:
    return "es_" + uuid.uuid4().hex[:16]


def build_evidence_pack(
    db: Session,
    *,
    disease_id: str,
    geo_id: str,
    metric: str,
    period_end: str | None,
    time_mode: str = "unified",
    view: str = "rate_per_100k",
) -> dict[str, Any]:
    detail = region_detail(
        db, geo_id, disease_id=disease_id, metric=metric, period_end=period_end, time_mode=time_mode, view=view
    )
    current = detail.get("current") or {}
    series = detail.get("series") or []
    evidence = []

    def add(kind: str, payload: dict[str, Any]) -> str:
        item_id = f"e{len(evidence)+1}"
        evidence.append({"id": item_id, "kind": kind, **payload})
        return item_id

    add(
        "selection",
        {
            "disease_id": disease_id,
            "geo_id": geo_id,
            "geo_name": detail.get("name"),
            "metric": metric,
            "view": view,
            "period_end": current.get("period_end") or period_end,
            "time_mode": time_mode,
        },
    )
    add(
        "observation",
        {
            "value": current.get("count"),
            "rate_per_100k": current.get("rate_per_100k") or current.get("value"),
            "unit": current.get("unit"),
            "data_status": current.get("data_status"),
            "period_start": current.get("period_start"),
            "period_end": current.get("period_end"),
            "issued_at": current.get("issued_at"),
            "source_id": current.get("source_id"),
            "quality_flag": current.get("quality_flag"),
        },
    )
    if current.get("change") is not None or current.get("change_status"):
        add("change", {"change": current.get("change"), "change_status": current.get("change_status")})
    if detail.get("population"):
        add("population", detail["population"])
    if series:
        add("series_tail", {"points": series[-8:]})
    if detail.get("forecasts"):
        add("forecast", {"items": detail["forecasts"][:4], "note": "预测不是观测，不能与病例数混用。"})
    add(
        "quality",
        {
            "data_status": current.get("data_status"),
            "lag_days": current.get("lag_days"),
            "representative_point": detail.get("representative_point"),
            "note": "无数据、零值、过期、未报告必须区分。地图着色不是综合风险分数。",
        },
    )
    add(
        "definition",
        {
            "definition": detail.get("definition"),
            "source_url": detail.get("source_url"),
            "disputed_note": detail.get("disputed_note"),
            "comparability": "国家之间监测定义、检测与报告延迟不同，不能直接当作感染水平排名。",
        },
    )
    current_stable = {k: v for k, v in current.items() if k not in {"color", "bin", "label"}}
    pack = {
        "disease_id": disease_id,
        "geo_id": geo_id,
        "metric": metric,
        "view": view,
        "period_end": current.get("period_end") or period_end,
        "time_mode": time_mode,
        "current": current_stable,
        "quality": {"note": "见 evidence 中 quality 项"},
        "evidence": evidence,
        "rules": [
            "AI 不得修改原始数据、地图阈值、模型参数或自动发布。",
            "正式分析只能引用已确认版本。",
        ],
    }
    pack["pack_hash"] = canonical_hash(
        {
            "disease_id": pack["disease_id"],
            "geo_id": pack["geo_id"],
            "metric": pack["metric"],
            "view": pack["view"],
            "period_end": pack["period_end"],
            "time_mode": pack["time_mode"],
            "evidence": pack["evidence"],
        }
    )
    return pack


def _store_snapshot(db: Session, pack: dict[str, Any]) -> EvidenceSnapshot:
    digest = pack.get("pack_hash") or canonical_hash(pack)
    existing = db.scalars(select(EvidenceSnapshot).where(EvidenceSnapshot.snapshot_hash == digest)).first()
    if existing:
        return existing
    row = EvidenceSnapshot(id=_snapshot_id(), snapshot_hash=digest, created_at=utcnow(), payload=pack)
    db.add(row)
    db.flush()
    return row


def _allowed_numbers(pack: dict[str, Any]) -> set[str]:
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, bool):
            return
        elif isinstance(node, int):
            found.add(str(node))
        elif isinstance(node, float):
            found.add(str(node))
            found.add(f"{node:.2f}")
            found.add(f"{node:.1f}")
            found.add(f"{node:.4g}")

    walk(pack)
    return found


def validate_structured(structured: dict[str, Any], pack: dict[str, Any]) -> dict[str, Any]:
    errors = []
    if not structured:
        return {"ok": False, "errors": ["模型未返回可解析 JSON。"], "confirmable": False}
    for field in REQUIRED_FIELDS:
        if field not in structured:
            errors.append(f"缺少字段 {field}")
    evidence_ids = {item["id"] for item in pack.get("evidence") or []}
    cited = []
    for item in structured.get("citations") or []:
        if isinstance(item, dict):
            cited.append(str(item.get("evidence_id") or ""))
        else:
            cited.append(str(item))
    for block_name in ("observations", "possible_explanations", "uncertainties", "data_quality_issues", "suggested_checks"):
        for item in structured.get(block_name) or []:
            if not isinstance(item, dict):
                errors.append(f"{block_name} 必须是带 evidence_ids 的对象")
                continue
            for eid in item.get("evidence_ids") or []:
                cited.append(str(eid))
    unknown = sorted({eid for eid in cited if eid and eid not in evidence_ids})
    if unknown:
        errors.append(f"引用了不存在的 evidence_id: {unknown}")
    important = (structured.get("observations") or []) + (structured.get("possible_explanations") or [])
    missing_ref = [item for item in important if isinstance(item, dict) and not item.get("evidence_ids")]
    if missing_ref:
        errors.append("重要结论缺少 evidence_ids")
    allowed = _allowed_numbers(pack)
    text_blobs = [str(structured.get("summary") or "")]
    for block_name in REQUIRED_FIELDS:
        text_blobs.append(json_text(structured.get(block_name)))
    blob = re.sub(r"\be\d+\b", " ", " ".join(text_blobs))
    numbers = re.findall(r"-?\d+\.?\d*", blob)
    invented = []
    for number in numbers:
        if number in {"0", "1", "2", "10", "100", "100000"}:
            continue
        if number not in allowed and f"{float(number):.4g}" not in allowed and f"{float(number):.2f}" not in allowed:
            invented.append(number)
    invented = sorted(set(invented))
    if invented:
        errors.append(f"出现证据包中不存在的数值，需修正: {invented[:12]}")
    return {"ok": not errors, "errors": errors, "confirmable": not errors, "invented_numbers": invented}


def json_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        import json

        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _record_payload(db: Session, row: AnalysisRecord) -> dict[str, Any]:
    revisions = list(
        db.scalars(select(AnalysisRevision).where(AnalysisRevision.analysis_id == row.id).order_by(AnalysisRevision.revision_no)).all()
    )
    decisions = list(db.scalars(select(ReviewDecision).where(ReviewDecision.analysis_id == row.id).order_by(ReviewDecision.created_at)).all())
    current = next((item for item in revisions if item.id == row.current_revision_id), revisions[-1] if revisions else None)
    confirmed = next((item for item in revisions if item.id == row.confirmed_revision_id), None)
    snapshot = db.get(EvidenceSnapshot, row.snapshot_id)
    return {
        "id": row.id,
        "parent_id": row.parent_id,
        "mode": row.mode,
        "status": row.status,
        "status_label": _status_label(row.status, row.mode),
        "disease_id": row.disease_id,
        "geo_id": row.geo_id,
        "metric": row.metric,
        "period_start": row.period_start,
        "period_end": row.period_end,
        "snapshot_id": row.snapshot_id,
        "snapshot_hash": row.snapshot_hash,
        "provider": row.provider,
        "model": row.model,
        "operator_name": row.operator_name,
        "identity_note": row.identity_note,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "current_revision_id": row.current_revision_id,
        "confirmed_revision_id": row.confirmed_revision_id,
        "draft_banner": "AI草稿，待人工确认" if row.mode == "ai" and row.status != "confirmed" else None,
        "official_ok": row.status == "confirmed",
        "source_note": "AI辅助、人工审核" if row.mode == "ai" and row.status == "confirmed" else ("手动分析" if row.mode == "manual" else "AI草稿"),
        "current": _revision_payload(current) if current else None,
        "confirmed": _revision_payload(confirmed) if confirmed else None,
        "revisions": [_revision_payload(item) for item in revisions],
        "decisions": [
            {
                "id": item.id,
                "action": item.action,
                "reviewer": item.reviewer,
                "comment": item.comment,
                "expected_snapshot_hash": item.expected_snapshot_hash,
                "created_at": item.created_at.isoformat(),
                "identity_note": item.identity_note,
            }
            for item in decisions
        ],
        "evidence": snapshot.payload if snapshot else None,
        "extra": row.extra or {},
    }


def _revision_payload(row: AnalysisRevision) -> dict[str, Any]:
    return {
        "id": row.id,
        "revision_no": row.revision_no,
        "source": row.source,
        "raw_text": row.raw_text,
        "structured": row.structured,
        "validation": row.validation,
        "created_at": row.created_at.isoformat(),
        "created_by": row.created_by,
    }


def _status_label(status: str, mode: str) -> str:
    labels = {
        "generating": "生成中",
        "pending_review": "待审核",
        "needs_correction": "需修正后才能确认",
        "confirmed": "已确认",
        "rejected": "已驳回",
        "draft": "草稿",
    }
    return labels.get(status, status)


def create_ai_analysis(
    db: Session,
    *,
    disease_id: str,
    geo_id: str,
    metric: str,
    period_end: str | None,
    time_mode: str = "unified",
    view: str = "rate_per_100k",
    parent_id: str | None = None,
    operator_name: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    operator = operator_name or settings.aerp_operator_name
    pack = build_evidence_pack(db, disease_id=disease_id, geo_id=geo_id, metric=metric, period_end=period_end, time_mode=time_mode, view=view)
    snapshot = _store_snapshot(db, pack)
    row = AnalysisRecord(
        id=_analysis_id(),
        parent_id=parent_id,
        mode="ai",
        status="generating",
        disease_id=disease_id,
        geo_id=geo_id,
        metric=metric,
        period_start=(pack.get("current") or {}).get("period_start") or "",
        period_end=str(pack.get("period_end") or period_end or ""),
        snapshot_id=snapshot.id,
        snapshot_hash=snapshot.snapshot_hash,
        provider="",
        model="",
        operator_name=operator,
        identity_note=IDENTITY_NOTE,
        created_at=utcnow(),
        updated_at=utcnow(),
        extra={"time_mode": time_mode, "view": view},
    )
    db.add(row)
    db.flush()
    result = ai_provider.complete(pack)
    if not result.get("ok"):
        row.status = "rejected" if False else "needs_correction"
        row.provider = str(result.get("provider") or "")
        row.model = str(result.get("model") or "")
        row.updated_at = utcnow()
        row.extra = {"error": result}
        db.commit()
        payload = _record_payload(db, row)
        payload["error"] = result
        return payload
    structured = result.get("structured") or {}
    validation = validate_structured(structured, pack)
    status = "pending_review" if validation.get("confirmable") else "needs_correction"
    revision = AnalysisRevision(
        analysis_id=row.id,
        revision_no=1,
        source="ai",
        raw_text=result.get("raw_text") or "",
        structured=structured,
        validation=validation,
        created_at=utcnow(),
        created_by=result.get("model") or "ai",
    )
    db.add(revision)
    db.flush()
    row.status = status
    row.provider = result.get("provider") or ""
    row.model = result.get("model") or ""
    row.current_revision_id = revision.id
    row.updated_at = utcnow()
    row.extra = {
        **(row.extra or {}),
        "latency_ms": result.get("latency_ms"),
        "usage": result.get("usage"),
        "live_verified": result.get("live_verified"),
        "note": result.get("note"),
    }
    db.commit()
    return _record_payload(db, row)


def create_manual_analysis(
    db: Session,
    *,
    disease_id: str,
    geo_id: str,
    metric: str,
    period_end: str | None,
    time_mode: str = "unified",
    view: str = "rate_per_100k",
    structured: dict[str, Any],
    as_draft: bool = True,
    operator_name: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    operator = operator_name or settings.aerp_operator_name
    pack = build_evidence_pack(db, disease_id=disease_id, geo_id=geo_id, metric=metric, period_end=period_end, time_mode=time_mode, view=view)
    snapshot = _store_snapshot(db, pack)
    validation = validate_structured(structured, pack)
    row = AnalysisRecord(
        id=_analysis_id(),
        mode="manual",
        status="draft" if as_draft else ("pending_review" if validation.get("confirmable") else "needs_correction"),
        disease_id=disease_id,
        geo_id=geo_id,
        metric=metric,
        period_start=(pack.get("current") or {}).get("period_start") or "",
        period_end=str(pack.get("period_end") or period_end or ""),
        snapshot_id=snapshot.id,
        snapshot_hash=snapshot.snapshot_hash,
        provider="manual",
        model="",
        operator_name=operator,
        identity_note=IDENTITY_NOTE,
        created_at=utcnow(),
        updated_at=utcnow(),
        extra={"source": "manual", "time_mode": time_mode, "view": view},
    )
    db.add(row)
    db.flush()
    revision = AnalysisRevision(
        analysis_id=row.id,
        revision_no=1,
        source="manual",
        raw_text=json_text(structured),
        structured=structured,
        validation=validation,
        created_at=utcnow(),
        created_by=operator,
    )
    db.add(revision)
    db.flush()
    row.current_revision_id = revision.id
    db.commit()
    return _record_payload(db, row)


def get_analysis(db: Session, analysis_id: str) -> dict[str, Any] | None:
    row = db.get(AnalysisRecord, analysis_id)
    if row is None:
        return None
    return _record_payload(db, row)


def list_revisions(db: Session, analysis_id: str) -> list[dict[str, Any]]:
    rows = db.scalars(select(AnalysisRevision).where(AnalysisRevision.analysis_id == analysis_id).order_by(AnalysisRevision.revision_no)).all()
    return [_revision_payload(item) for item in rows]


def review_analysis(
    db: Session,
    analysis_id: str,
    *,
    action: str,
    expected_revision_id: int,
    snapshot_hash: str,
    comment: str = "",
    edited: dict[str, Any] | None = None,
    operator_name: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    operator = operator_name or settings.aerp_operator_name
    row = db.get(AnalysisRecord, analysis_id)
    if row is None:
        return {"error": "not_found", "message": "分析不存在"}
    if row.status in TERMINAL and action != "reject":
        return {"error": "terminal", "message": "已确认或已驳回的记录不能覆盖。请重新生成新记录。", "record": _record_payload(db, row)}
    current = db.get(AnalysisRevision, row.current_revision_id) if row.current_revision_id else None
    if current is None or current.id != expected_revision_id:
        return {"error": "revision_mismatch", "message": "确认必须针对当前修订版本。", "record": _record_payload(db, row)}
    if snapshot_hash != row.snapshot_hash:
        return {"error": "stale_snapshot", "message": "提交的快照哈希与记录不一致，不能确认过期内容。", "record": _record_payload(db, row)}
    if action != "reject":
        live_pack = build_evidence_pack(
            db,
            disease_id=row.disease_id,
            geo_id=row.geo_id,
            metric=row.metric,
            period_end=row.period_end or None,
            time_mode=(row.extra or {}).get("time_mode") or "unified",
            view=(row.extra or {}).get("view") or "rate_per_100k",
        )
        if live_pack.get("pack_hash") != row.snapshot_hash:
            row.extra = {**(row.extra or {}), "stale": True, "live_snapshot_hash": live_pack.get("pack_hash")}
            db.commit()
            return {"error": "snapshot_changed", "message": "数据快照已变化，旧确认不适用于新数据。请重新分析。", "record": _record_payload(db, row)}

    if action == "edit_confirm" and edited is not None:
        snapshot = db.get(EvidenceSnapshot, row.snapshot_id)
        validation = validate_structured(edited, snapshot.payload if snapshot else {})
        next_no = current.revision_no + 1
        revision = AnalysisRevision(
            analysis_id=row.id,
            revision_no=next_no,
            source="human_edit",
            raw_text=json_text(edited),
            structured=edited,
            validation=validation,
            created_at=utcnow(),
            created_by=operator,
        )
        db.add(revision)
        db.flush()
        row.current_revision_id = revision.id
        current = revision
        if not validation.get("confirmable"):
            row.status = "needs_correction"
            row.updated_at = utcnow()
            db.add(
                ReviewDecision(
                    analysis_id=row.id,
                    revision_id=revision.id,
                    action="edit_blocked",
                    reviewer=operator,
                    comment=comment,
                    expected_snapshot_hash=snapshot_hash,
                    created_at=utcnow(),
                    identity_note=IDENTITY_NOTE,
                )
            )
            db.commit()
            return {"error": "needs_correction", "message": "编辑后仍有证据不一致，不能确认。", "record": _record_payload(db, row)}

    if action in {"confirm", "edit_confirm"}:
        if current.validation and not current.validation.get("confirmable"):
            return {"error": "needs_correction", "message": "数值或引用不匹配，不能进入已确认状态。", "record": _record_payload(db, row)}
        if row.status not in CONFIRMABLE and not (action == "edit_confirm" and current.source == "human_edit"):
            if row.status != "pending_review":
                return {"error": "illegal_transition", "message": "只有待审核且通过核验的版本可以确认。", "record": _record_payload(db, row)}
        row.status = "confirmed"
        row.confirmed_revision_id = current.id
        row.updated_at = utcnow()
        db.add(
            ReviewDecision(
                analysis_id=row.id,
                revision_id=current.id,
                action=action,
                reviewer=operator,
                comment=comment,
                expected_snapshot_hash=snapshot_hash,
                created_at=utcnow(),
                identity_note=IDENTITY_NOTE,
            )
        )
        db.commit()
        return _record_payload(db, row)

    if action == "reject":
        row.status = "rejected"
        row.updated_at = utcnow()
        db.add(
            ReviewDecision(
                analysis_id=row.id,
                revision_id=current.id,
                action="reject",
                reviewer=operator,
                comment=comment,
                expected_snapshot_hash=snapshot_hash,
                created_at=utcnow(),
                identity_note=IDENTITY_NOTE,
            )
        )
        db.commit()
        return _record_payload(db, row)

    if action == "save_draft" and edited is not None:
        snapshot = db.get(EvidenceSnapshot, row.snapshot_id)
        validation = validate_structured(edited, snapshot.payload if snapshot else {})
        revision = AnalysisRevision(
            analysis_id=row.id,
            revision_no=current.revision_no + 1,
            source="manual" if row.mode == "manual" else "human_edit",
            raw_text=json_text(edited),
            structured=edited,
            validation=validation,
            created_at=utcnow(),
            created_by=operator,
        )
        db.add(revision)
        db.flush()
        row.current_revision_id = revision.id
        row.status = "draft" if row.mode == "manual" else ("pending_review" if validation.get("confirmable") else "needs_correction")
        row.updated_at = utcnow()
        db.commit()
        return _record_payload(db, row)

    return {"error": "unknown_action", "message": f"不支持的审核动作 {action}"}


def export_analysis(db: Session, analysis_id: str) -> dict[str, Any]:
    payload = get_analysis(db, analysis_id)
    if payload is None:
        return {"error": "not_found"}
    official = payload["status"] == "confirmed"
    return {
        "analysis_id": analysis_id,
        "official": official,
        "review_required_banner": None if official else "未审核草稿，不得作为正式报告。",
        "source_note": payload.get("source_note"),
        "identity_note": payload.get("identity_note"),
        "record": payload,
    }


def analyses_for_geo(db: Session, geo_id: str, disease_id: str, metric: str) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(AnalysisRecord)
        .where(AnalysisRecord.geo_id == geo_id, AnalysisRecord.disease_id == disease_id, AnalysisRecord.metric == metric)
        .order_by(AnalysisRecord.created_at.desc())
    ).all()
    return [_record_payload(db, row) for row in rows[:30]]
