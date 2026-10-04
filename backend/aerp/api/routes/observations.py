from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from aerp.core.timeutil import as_timestamp
from aerp.db.session import get_db
from aerp.services.observations import overview, visible_observations

router = APIRouter()


@router.get("/observations")
def observations(
    disease_id: str,
    metric: str = "hospital_admissions",
    region_id: str | None = None,
    cutoff: str | None = None,
    replay_mode: str = "vintage",
    db: Session = Depends(get_db),
):
    if cutoff is None:
        raise HTTPException(status_code=400, detail="必须提供 cutoff，避免默认读到全部未来版本")
    frame = visible_observations(
        db,
        disease_id=disease_id,
        region_id=region_id,
        metric=metric,
        cutoff=cutoff,
        replay_mode=replay_mode,
    )
    records = []
    for row in frame.itertuples(index=False):
        records.append(
            {
                "disease_id": row.disease_id,
                "region_id": row.region_id,
                "metric": row.metric,
                "period_start": as_timestamp(row.period_start).date().isoformat(),
                "period_end": as_timestamp(row.period_end).date().isoformat(),
                "issued_at": as_timestamp(row.issued_at).isoformat(sep=" ", timespec="seconds"),
                "value": None if row.value != row.value else row.value,
                "unit": row.unit,
                "source_id": row.source_id,
                "quality_flag": row.quality_flag,
                "import_batch_id": row.import_batch_id,
            }
        )
    return {
        "cutoff": cutoff,
        "replay_mode": replay_mode,
        "count": len(records),
        "items": records,
        "note": "只返回截止时间当时可见的版本。缺失保持为空，不会变成 0。",
    }


@router.get("/overview")
def overview_view(
    disease_id: str,
    region_id: str,
    cutoff: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return overview(db, disease_id, region_id, cutoff)
