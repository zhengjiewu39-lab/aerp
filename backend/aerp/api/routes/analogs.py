from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from aerp.api.schemas import AnalogMatchRequest
from aerp.core.analogs import analog_summaries, match_values
from aerp.core.series import prepare_weekly
from aerp.core.vintages import select_snapshot
from aerp.db.session import get_db
from aerp.services.observations import list_regions, observations_frame, region_context_map

router = APIRouter()


@router.get("/analogs")
def list_analogs():
    return {
        "disclaimer": analog_summaries()[0]["disclaimer"] if analog_summaries() else "",
        "items": analog_summaries(),
    }


@router.post("/analogs/match")
def match_analog(payload: AnalogMatchRequest, db: Session = Depends(get_db)):
    frame = observations_frame(db, disease_id=payload.disease_id, region_id=payload.region_id, metric=payload.metric)
    if frame.empty:
        raise HTTPException(status_code=404, detail="该地区没有可用于形态匹配的观测")
    cutoff = payload.cutoff or frame["issued_at"].max()
    visible = select_snapshot(frame, cutoff, payload.replay_mode)
    _index, values = prepare_weekly(visible)
    contexts = region_context_map(db)
    peers = {}
    for region in list_regions(db):
        if region["id"] == payload.region_id:
            continue
        other = observations_frame(db, disease_id=payload.disease_id, region_id=region["id"], metric=payload.metric)
        other_visible = select_snapshot(other, cutoff, payload.replay_mode)
        _other_index, other_values = prepare_weekly(other_visible)
        peers[region["id"]] = {
            "values": other_values,
            "context": contexts.get(region["id"]) or {},
            "name": region["name"],
        }
    matched = match_values(
        values,
        context=contexts.get(payload.region_id) or {},
        peers=peers,
        horizons=payload.horizons,
    )
    region = next((item for item in list_regions(db) if item["id"] == payload.region_id), None)
    return {
        **matched,
        "disease_id": payload.disease_id,
        "region": region,
        "cutoff": str(cutoff),
        "metric": payload.metric,
    }
