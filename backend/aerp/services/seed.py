from __future__ import annotations

import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from aerp.config import get_settings
from aerp.core.hashing import sha256_text
from aerp.core.diseases import DISEASE_SPECS
from aerp.core.synthetic import BATCH_ID, REGIONS, SOURCE_ID, generate_synthetic
from aerp.core.synthetic_measles import BATCH_ID as MEASLES_BATCH
from aerp.core.synthetic_measles import DISEASE_ID as MEASLES_ID
from aerp.core.synthetic_measles import REGIONS as MEASLES_REGIONS
from aerp.core.synthetic_measles import SOURCE_ID as MEASLES_SOURCE
from aerp.core.synthetic_measles import generate_measles_demo
from aerp.core.timeutil import utcnow
from aerp.db.models import (
    DataSource,
    Disease,
    ImportBatch,
    ObservationVintage,
    Region,
    SimulationLabel,
    SimulationTruth,
)


def _finite_or_none(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def seed_history(db: Session) -> dict:
    """历史疫情资料卡为静态配置，不写入编造病例序列。"""
    del db
    return {
        "seeded": True,
        "message": "历史资料卡由 /api/history-cards 提供；无可训练伪造序列。形态类比使用归一化曲线，不是病例数。",
    }


def refresh_region_context(db: Session, regions: list[dict] | None = None) -> None:
    specs = regions or [
        {
            "id": item["region_id"],
            "name": item["name"],
            "population": item.get("population"),
            "scenario": item.get("scenario"),
            "note": item.get("scenario"),
            "context": item.get("context") or {},
        }
        for item in REGIONS
    ]
    for spec in specs:
        region_id = spec.get("id") or spec.get("region_id")
        if not region_id:
            continue
        row = db.get(Region, region_id)
        if row is None:
            continue
        if spec.get("context"):
            row.context_json = spec["context"]


def seed_synthetic(db: Session, seed: int | None = None, force: bool = False, replace: bool | None = None) -> dict:
    settings = get_settings()
    seed_value = int(settings.aerp_demo_seed if seed is None else seed)
    if replace is not None:
        force = bool(replace)
    existing = db.get(ImportBatch, BATCH_ID)
    if existing and not force:
        refresh_region_context(db)
        db.commit()
        return {"seeded": False, "message": "合成演示数据已存在", "batch_id": BATCH_ID, "seed": seed_value}

    if force:
        db.query(ObservationVintage).filter(ObservationVintage.import_batch_id == BATCH_ID).delete()
        db.query(SimulationTruth).delete()
        db.query(SimulationLabel).delete()
        if existing:
            db.delete(existing)
        db.flush()

    bundle = generate_synthetic(seed=seed_value)
    disease = db.get(Disease, bundle.disease["id"])
    if disease is None:
        disease = Disease(
            id=bundle.disease["id"],
            name=bundle.disease["name"],
            description=bundle.disease["description"],
            is_synthetic=True,
        )
        db.add(disease)
    else:
        disease.name = bundle.disease["name"]
        disease.description = bundle.disease["description"]
        disease.is_synthetic = True

    for region in bundle.regions:
        row = db.get(Region, region["id"])
        if row is None:
            db.add(
                Region(
                    id=region["id"],
                    name=region["name"],
                    population=region.get("population"),
                    scenario=region.get("scenario"),
                    note=region.get("note", ""),
                    context_json=region.get("context") or {},
                )
            )
        else:
            row.name = region["name"]
            row.population = region.get("population")
            row.scenario = region.get("scenario")
            row.note = region.get("note", "")
            row.context_json = region.get("context") or {}

    source = db.get(DataSource, SOURCE_ID)
    if source is None:
        db.add(
            DataSource(
                id=SOURCE_ID,
                name=bundle.source["name"],
                url=bundle.source.get("url", ""),
                license=bundle.source.get("license", ""),
                update_note=bundle.source.get("update_note", ""),
                connector_status=bundle.source.get("connector_status", "available"),
            )
        )
    else:
        source.name = bundle.source["name"]
        source.license = bundle.source.get("license", "")
        source.update_note = bundle.source.get("update_note", "")
        source.connector_status = bundle.source.get("connector_status", "available")

    db.flush()
    payload_hash = sha256_text(bundle.observations.to_json(orient="records", date_format="iso"))
    db.add(
        ImportBatch(
            id=BATCH_ID,
            source_id=SOURCE_ID,
            file_hash=payload_hash,
            filename=f"synthetic_seed_{seed_value}.parquet-like",
            imported_at=utcnow(),
            row_count=int(len(bundle.observations)),
            note=bundle.note,
            is_synthetic=True,
        )
    )
    db.flush()

    for row in bundle.observations.itertuples(index=False):
        db.add(
            ObservationVintage(
                disease_id=row.disease_id,
                region_id=row.region_id,
                period_start=row.period_start.to_pydatetime(),
                period_end=row.period_end.to_pydatetime(),
                issued_at=row.issued_at.to_pydatetime(),
                metric=row.metric,
                value=_finite_or_none(row.value),
                unit=row.unit,
                source_id=row.source_id,
                quality_flag=row.quality_flag,
                import_batch_id=BATCH_ID,
            )
        )
    for row in bundle.truth.itertuples(index=False):
        db.add(
            SimulationTruth(
                region_id=row.region_id,
                period_start=row.period_start.to_pydatetime(),
                period_end=row.period_end.to_pydatetime(),
                metric=row.metric,
                value=float(row.value),
            )
        )
    for row in bundle.labels.itertuples(index=False):
        db.add(
            SimulationLabel(
                region_id=row.region_id,
                period_start=row.period_start.to_pydatetime(),
                period_end=row.period_end.to_pydatetime(),
                category=row.category,
                note=row.note,
            )
        )
    db.commit()
    return {
        "seeded": True,
        "message": "已生成合成演示数据",
        "batch_id": BATCH_ID,
        "seed": seed_value,
        "n_observations": int(len(bundle.observations)),
        "n_regions": len(bundle.regions),
        "n_weeks": bundle.n_weeks,
        "clock": bundle.clock.isoformat(),
        "note": bundle.note,
    }


def ensure_disease_registry(db: Session) -> None:
    for spec in DISEASE_SPECS.values():
        row = db.get(Disease, spec.disease_id)
        if row is None:
            db.add(
                Disease(
                    id=spec.disease_id,
                    name=spec.name_zh,
                    description=f"{spec.name_en}。{spec.status_label}。{spec.notes}",
                    is_synthetic=spec.is_synthetic,
                )
            )
        else:
            row.name = spec.name_zh
            row.description = f"{spec.name_en}。{spec.status_label}。{spec.notes}"
            if spec.is_synthetic:
                row.is_synthetic = True


def seed_measles(db: Session, seed: int = 7, force: bool = False) -> dict:
    existing = db.get(ImportBatch, MEASLES_BATCH)
    if existing and not force:
        return {"seeded": False, "message": "麻疹合成示例已存在", "batch_id": MEASLES_BATCH}
    ensure_disease_registry(db)
    if force and existing:
        db.query(ObservationVintage).filter(ObservationVintage.import_batch_id == MEASLES_BATCH).delete()
        db.delete(existing)
        db.flush()
    source = db.get(DataSource, MEASLES_SOURCE)
    if source is None:
        db.add(
            DataSource(
                id=MEASLES_SOURCE,
                name="麻疹合成教学数据",
                url="",
                license="本仓库生成",
                update_note="明确标注为合成，不是真实监测。",
                connector_status="available",
            )
        )
    for region in MEASLES_REGIONS:
        if db.get(Region, region["region_id"]) is None:
            db.add(
                Region(
                    id=region["region_id"],
                    name=region["name"],
                    population=region.get("population"),
                    scenario="synthetic_measles",
                    note=region.get("note", ""),
                    context_json={"is_demo_label": True, "note": "合成麻疹教学区"},
                )
            )
    db.flush()
    bundle = generate_measles_demo(seed=seed)
    payload_hash = sha256_text(bundle.observations.to_json(orient="records", date_format="iso"))
    db.add(
        ImportBatch(
            id=MEASLES_BATCH,
            source_id=MEASLES_SOURCE,
            file_hash=payload_hash,
            filename=f"synthetic_measles_{seed}.json",
            imported_at=utcnow(),
            row_count=int(len(bundle.observations)),
            note=bundle.note,
            is_synthetic=True,
        )
    )
    db.flush()
    for row in bundle.observations.itertuples(index=False):
        db.add(
            ObservationVintage(
                disease_id=row.disease_id,
                region_id=row.region_id,
                period_start=row.period_start.to_pydatetime(),
                period_end=row.period_end.to_pydatetime(),
                issued_at=row.issued_at.to_pydatetime(),
                metric=row.metric,
                value=_finite_or_none(row.value),
                unit=row.unit,
                source_id=row.source_id,
                quality_flag=row.quality_flag,
                import_batch_id=MEASLES_BATCH,
            )
        )
    db.commit()
    return {
        "seeded": True,
        "message": bundle.note,
        "batch_id": MEASLES_BATCH,
        "n_observations": int(len(bundle.observations)),
        "is_synthetic": True,
        "disease_id": MEASLES_ID,
    }


def ensure_seeded(db: Session) -> None:
    settings = get_settings()
    ensure_disease_registry(db)
    db.commit()
    if not settings.aerp_auto_seed:
        return
    if db.scalar(select(ImportBatch.id).where(ImportBatch.id == BATCH_ID)):
        if db.get(Region, "R5") is None:
            seed_synthetic(db, seed=settings.aerp_demo_seed, force=True, replace=True)
            seed_measles(db, force=False)
            return
        refresh_region_context(db)
        seed_measles(db, force=False)
        db.commit()
        return
    seed_synthetic(db, seed=settings.aerp_demo_seed)
    seed_measles(db, force=False)
