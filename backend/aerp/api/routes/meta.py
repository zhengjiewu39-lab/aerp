from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from aerp import __version__
from aerp.core.disclaimer import DISCLAIMER
from aerp.core.models.catalog import MODEL_CARDS
from aerp.core.models.transfer import TransferModel
from aerp.db.session import get_db
from aerp.services.connectors import list_connectors
from aerp.services.history_cards import HISTORY_CARDS
from aerp.services.observations import data_mode_banner, list_diseases, list_regions, list_sources
from aerp.services.seed import seed_synthetic

router = APIRouter()


@router.get("/health")
def health(db: Session = Depends(get_db)):
    banner = data_mode_banner(db)
    return {
        "status": "ok",
        "version": __version__,
        "disclaimer": DISCLAIMER,
        "banner": banner["banner"],
        "data_mode": banner["mode"],
    }


@router.get("/meta")
def meta(db: Session = Depends(get_db)):
    banner = data_mode_banner(db)
    return {
        "name": "Adaptive Epidemic Research Platform",
        "short_name": "AERP",
        "version": __version__,
        "disclaimer": DISCLAIMER,
        "banner": banner,
        "research_question": "在监测数据修订或疫情趋势突然变化时，先识别变化类型再更新模型，是否比直接在线更新更可靠？",
        "transfer": TransferModel().serialize_metadata(),
    }


@router.get("/diseases")
def diseases(db: Session = Depends(get_db)):
    return list_diseases(db)


@router.get("/regions")
def regions(db: Session = Depends(get_db)):
    return list_regions(db)


@router.get("/sources")
def sources(db: Session = Depends(get_db)):
    return list_sources(db)


@router.get("/models")
def models():
    return MODEL_CARDS


@router.get("/history-cards")
def history_cards():
    return HISTORY_CARDS


@router.get("/connectors")
def connectors():
    return list_connectors()


@router.post("/demo/seed")
def demo_seed(force: bool = False, replace: bool = False, db: Session = Depends(get_db)):
    return seed_synthetic(db, force=force or replace, replace=replace)
