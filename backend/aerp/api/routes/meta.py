from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from aerp import __version__
from aerp.core.disclaimer import DISCLAIMER
from aerp.core.analogs import analog_summaries
from aerp.core.diseases import list_disease_specs
from aerp.core.models.catalog import MODEL_CARDS
from aerp.core.models.transfer import TransferModel
from aerp.core.recommend import recommend_models
from aerp.research.transfer_run import generate_paired_respiratory, run_transfer_comparison
from aerp.services.dengue import adapter_status as dengue_status
from aerp.db.session import get_db
from aerp.services.connectors import list_connectors
from aerp.services.history_cards import HISTORY_CARDS
from aerp.services.observations import data_mode_banner, list_diseases, list_regions, list_sources
from aerp.services.seed import seed_measles, seed_synthetic

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
        "diseases": list_disease_specs(),
        "transfer": TransferModel().serialize_metadata(),
        "analog": {
            "available": True,
            "model_id": "analog",
            "disclaimer": analog_summaries()[0]["disclaimer"] if analog_summaries() else "",
            "n_templates": len(analog_summaries()),
        },
    }


@router.get("/diseases")
def diseases(db: Session = Depends(get_db)):
    return list_diseases(db)


@router.get("/regions")
def regions(disease_id: str | None = None, db: Session = Depends(get_db)):
    return list_regions(db, disease_id=disease_id)


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


@router.post("/demo/seed-measles")
def demo_seed_measles(force: bool = False, db: Session = Depends(get_db)):
    return seed_measles(db, force=force)


@router.get("/recommend")
def recommend(disease_id: str, target: str | None = None, n_weeks: int = 0):
    return recommend_models(disease_id, target=target, n_weeks=n_weeks)


@router.get("/transfer/demo")
def transfer_demo():
    frames = generate_paired_respiratory()
    return run_transfer_comparison(
        frames["synth-covid-hosp"],
        {"synth-flu-hosp": frames["synth-flu-hosp"]},
        target_disease_id="synth-covid-hosp",
        region_id="PA",
        data_mode="synthetic_stress_test",
    )


@router.get("/dengue/status")
def dengue():
    return dengue_status()
