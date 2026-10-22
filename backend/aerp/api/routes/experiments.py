from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from aerp.api.schemas import ExperimentCreate
from aerp.db.models import Evaluation, Experiment, Forecast, ModelRun
from aerp.db.session import get_db
from aerp.services.experiment_engine import create_experiment, experiment_to_dict, export_experiment
from aerp.services.tasks import get_ablation_suite, submit_ablation_suite, submit_experiment

router = APIRouter()


@router.get("/experiments")
def list_experiments(db: Session = Depends(get_db)):
    rows = db.scalars(select(Experiment).order_by(Experiment.created_at.desc())).all()
    return [experiment_to_dict(row) for row in rows]


@router.post("/experiments")
def create(payload: ExperimentCreate, db: Session = Depends(get_db)):
    experiment = create_experiment(db, payload.model_dump())
    return experiment_to_dict(experiment)


@router.post("/experiments/ablation-suite")
def ablation_suite(payload: ExperimentCreate):
    """提交消融对照到后台线程，避免阻塞总览等普通 API。"""
    body = payload.model_dump()
    return submit_ablation_suite(body)


@router.get("/experiments/ablation-suite/{suite_id}")
def ablation_suite_status(suite_id: str):
    suite = get_ablation_suite(suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail="消融任务不存在或已因重启丢失")
    return suite


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str, db: Session = Depends(get_db)):
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="实验不存在")
    runs = db.scalars(select(ModelRun).where(ModelRun.experiment_id == experiment_id)).all()
    return {
        **experiment_to_dict(experiment),
        "runs": [
            {
                "id": run.id,
                "model_id": run.model_id,
                "status": run.status,
                "error_message": run.error_message,
                "trained_at": None if run.trained_at is None else run.trained_at.isoformat(sep=" ", timespec="seconds"),
                "metadata": run.metadata_json,
            }
            for run in runs
        ],
    }


@router.post("/experiments/{experiment_id}/run")
def run(experiment_id: str, db: Session = Depends(get_db)):
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="实验不存在")
    if experiment.status == "running":
        return {"queued": False, "message": "任务已在运行", "experiment_id": experiment_id, "status": experiment.status}
    experiment.status = "queued"
    db.commit()
    result = submit_experiment(experiment_id)
    return {**result, "status": "queued"}


@router.get("/experiments/{experiment_id}/export")
def export(experiment_id: str, db: Session = Depends(get_db)):
    try:
        payload = export_experiment(db, experiment_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{experiment_id}.json"'},
    )


@router.get("/forecasts")
def forecasts(experiment_id: str, region_id: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Forecast).where(Forecast.experiment_id == experiment_id)
    if region_id:
        stmt = stmt.where(Forecast.region_id == region_id)
    rows = db.scalars(stmt.order_by(Forecast.cutoff_time, Forecast.model_id, Forecast.horizon)).all()
    return [
        {
            "id": row.id,
            "model_id": row.model_id,
            "region_id": row.region_id,
            "metric": row.metric,
            "issued_at": row.issued_at.isoformat(sep=" ", timespec="seconds"),
            "cutoff_time": row.cutoff_time.isoformat(sep=" ", timespec="seconds"),
            "target_period_start": row.target_period_start.date().isoformat(),
            "target_period_end": row.target_period_end.date().isoformat(),
            "horizon": row.horizon,
            "median": row.q50,
            "interval_50": [row.q25, row.q75],
            "interval_80": [row.q10, row.q90],
            "interval_95": [row.q025, row.q975],
            "interval_method": row.interval_method,
            "quality_note": row.quality_note,
            "generated_at": row.generated_at.isoformat(sep=" ", timespec="seconds"),
            "weights": row.weights,
            "extra": row.extra or {},
        }
        for row in rows
    ]


@router.get("/evaluations")
def evaluations(experiment_id: str, db: Session = Depends(get_db)):
    rows = db.scalars(select(Evaluation).where(Evaluation.experiment_id == experiment_id)).all()
    return [
        {
            "id": row.id,
            "model_id": row.model_id,
            "region_id": row.region_id,
            "horizon": row.horizon,
            "target_period_end": row.target_period_end.date().isoformat(),
            "status": row.status,
            "actual_value": row.actual_value,
            "matured_at": None if row.matured_at is None else row.matured_at.isoformat(sep=" ", timespec="seconds"),
            "abs_error": row.abs_error,
            "wis": row.wis,
            "covered_50": row.covered_50,
            "covered_80": row.covered_80,
            "covered_95": row.covered_95,
            "width_50": row.width_50,
            "width_80": row.width_80,
            "width_95": row.width_95,
            "phase": row.phase,
            "notes": row.notes,
        }
        for row in rows
    ]
