from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from aerp.api.schemas import AlertReviewUpdate
from aerp.db.models import Alert
from aerp.db.session import get_db

router = APIRouter()

ALLOWED = {"unreviewed", "supported", "rejected", "needs_more_info"}


@router.get("/alerts")
def list_alerts(experiment_id: str | None = None, region_id: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Alert).order_by(Alert.created_at.desc())
    if experiment_id:
        stmt = stmt.where(Alert.experiment_id == experiment_id)
    if region_id:
        stmt = stmt.where(Alert.region_id == region_id)
    rows = db.scalars(stmt).all()
    return [
        {
            "id": row.id,
            "experiment_id": row.experiment_id,
            "region_id": row.region_id,
            "category": row.category,
            "level": row.level,
            "rule": row.rule,
            "metrics": row.metrics,
            "period_start": row.period_start.date().isoformat(),
            "period_end": row.period_end.date().isoformat(),
            "source_id": row.source_id,
            "import_batch_id": row.import_batch_id,
            "supporting_evidence": row.supporting_evidence,
            "conflicting_evidence": row.conflicting_evidence,
            "limitations": row.limitations,
            "affected_models": row.affected_models,
            "review_status": row.review_status,
            "created_at": row.created_at.isoformat(sep=" ", timespec="seconds"),
            "cutoff_time": None if row.cutoff_time is None else row.cutoff_time.isoformat(sep=" ", timespec="seconds"),
        }
        for row in rows
    ]


@router.patch("/alerts/{alert_id}")
def update_alert(alert_id: int, payload: AlertReviewUpdate, db: Session = Depends(get_db)):
    if payload.review_status not in ALLOWED:
        raise HTTPException(status_code=400, detail=f"review_status 必须是 {sorted(ALLOWED)}")
    row = db.get(Alert, alert_id)
    if row is None:
        raise HTTPException(status_code=404, detail="异常不存在")
    row.review_status = payload.review_status
    db.commit()
    return {"id": row.id, "review_status": row.review_status}
