from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from aerp.config import get_settings
from aerp.db.session import get_db
from aerp.services.analyses import (
    analyses_for_geo,
    create_ai_analysis,
    create_manual_analysis,
    export_analysis,
    get_analysis,
    list_revisions,
    review_analysis,
)
from aerp.services.ai_provider import provider_status
from aerp.services.geo_catalog import ensure_geo_catalog

router = APIRouter()


class AiRequest(BaseModel):
    disease_id: str
    geo_id: str
    metric: str
    period_end: str | None = None
    time_mode: str = "unified"
    view: str = "rate_per_100k"
    parent_id: str | None = None


class ManualRequest(BaseModel):
    disease_id: str
    geo_id: str
    metric: str
    period_end: str | None = None
    time_mode: str = "unified"
    view: str = "rate_per_100k"
    as_draft: bool = True
    structured: dict[str, Any] = Field(default_factory=dict)


class ReviewRequest(BaseModel):
    action: str
    expected_revision_id: int
    snapshot_hash: str
    comment: str = ""
    edited: dict[str, Any] | None = None


def _operator(x_aerp_operator: str | None) -> str:
    return x_aerp_operator or get_settings().aerp_operator_name


@router.get("/ai/status")
def ai_status():
    return provider_status()


@router.post("/analyses/ai")
def analyses_ai(body: AiRequest, db: Session = Depends(get_db), x_aerp_operator: str | None = Header(default=None)):
    ensure_geo_catalog(db)
    status = provider_status()
    if not status.get("configured"):
        raise HTTPException(503, {"message": "AI未配置/调用失败", "ai": status})
    return create_ai_analysis(
        db,
        disease_id=body.disease_id,
        geo_id=body.geo_id,
        metric=body.metric,
        period_end=body.period_end,
        time_mode=body.time_mode,
        view=body.view,
        parent_id=body.parent_id,
        operator_name=_operator(x_aerp_operator),
    )


@router.post("/analyses/manual")
def analyses_manual(body: ManualRequest, db: Session = Depends(get_db), x_aerp_operator: str | None = Header(default=None)):
    ensure_geo_catalog(db)
    return create_manual_analysis(
        db,
        disease_id=body.disease_id,
        geo_id=body.geo_id,
        metric=body.metric,
        period_end=body.period_end,
        time_mode=body.time_mode,
        view=body.view,
        structured=body.structured,
        as_draft=body.as_draft,
        operator_name=_operator(x_aerp_operator),
    )


@router.get("/analyses/{analysis_id}")
def analysis_get(analysis_id: str, db: Session = Depends(get_db)):
    payload = get_analysis(db, analysis_id)
    if payload is None:
        raise HTTPException(404, {"message": "分析不存在"})
    return payload


@router.post("/analyses/{analysis_id}/review")
def analysis_review(analysis_id: str, body: ReviewRequest, db: Session = Depends(get_db), x_aerp_operator: str | None = Header(default=None)):
    payload = review_analysis(
        db,
        analysis_id,
        action=body.action,
        expected_revision_id=body.expected_revision_id,
        snapshot_hash=body.snapshot_hash,
        comment=body.comment,
        edited=body.edited,
        operator_name=_operator(x_aerp_operator),
    )
    if payload.get("error"):
        raise HTTPException(409, payload)
    return payload


@router.get("/analyses/{analysis_id}/revisions")
def analysis_revisions(analysis_id: str, db: Session = Depends(get_db)):
    if get_analysis(db, analysis_id) is None:
        raise HTTPException(404, {"message": "分析不存在"})
    return list_revisions(db, analysis_id)


@router.get("/analyses/{analysis_id}/export")
def analysis_export(analysis_id: str, db: Session = Depends(get_db)):
    payload = export_analysis(db, analysis_id)
    if payload.get("error"):
        raise HTTPException(404, payload)
    return payload


@router.get("/map/regions/{geo_id}/analyses")
def region_analyses(geo_id: str, disease_id: str, metric: str, db: Session = Depends(get_db)):
    return analyses_for_geo(db, geo_id, disease_id, metric)
