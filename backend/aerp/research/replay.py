from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd
from sqlalchemy.orm import Session

from aerp.core.alerts_eval import development_thresholds, evaluate_joint_alerts
from aerp.core.change_labels import diagnosis_report
from aerp.core.hashing import hash_observations, sha256_text
from aerp.core.research_mode import RESEARCH_QUESTION
from aerp.core.timeutil import as_timestamp, utcnow
from aerp.db.models import DataSource, Disease, ImportBatch, ObservationVintage, Region
from aerp.research.dbutil import research_session
from aerp.research.runs import incomplete_artifacts, start_run, write_json, write_text
from aerp.research.splits import infer_splits
from aerp.services.experiment_engine import create_experiment, export_experiment, run_experiment
from aerp.services.flusight import (
    DEFAULT_RESEARCH_LOCATIONS,
    fetch_flusight_target,
    flu_season_origins,
    observations_from_flusight,
    subset_observations,
)
from aerp.services.flusight_models import fetch_and_score_baselines, list_hub_reference_dates
from aerp.services.observations import observations_frame

RESEARCH_ARMS = (
    "persistence",
    "retrain_glm",
    "pooled_glm",
    "hierarchical",
    "equal_weight",
    "dynamic_weight",
    "adaptive",
)
SOURCE_ID = "flusight-nhsn"
DISEASE_ID = "influenza"


def ensure_research_entities(db: Session, rows: pd.DataFrame) -> None:
    if db.get(Disease, DISEASE_ID) is None:
        db.add(
            Disease(
                id=DISEASE_ID,
                name="Influenza",
                description="FluSight / NHSN 周确诊流感住院",
                is_synthetic=False,
            )
        )
    if db.get(DataSource, SOURCE_ID) is None:
        db.add(
            DataSource(
                id=SOURCE_ID,
                name="FluSight NHSN",
                url="https://github.com/cdcepi/FluSight-forecast-hub",
                license="按 Hub README 与 CDC NHSN 条款",
                update_note="issued_at 来自 Hub as_of，经 2025-07-05 语义转换。",
                connector_status="available",
            )
        )
    names = {}
    if "region_name" in rows.columns:
        names = (
            rows.dropna(subset=["region_id"])
            .drop_duplicates("region_id")
            .set_index("region_id")["region_name"]
            .to_dict()
        )
    for region_id in sorted(set(rows["region_id"].astype(str))):
        if db.get(Region, region_id) is None:
            db.add(
                Region(
                    id=region_id,
                    name=str(names.get(region_id) or region_id),
                    population=None,
                    scenario=None,
                    note="FluSight 辖区。人口未写入时不输出人均指标。",
                    context_json={"source": "flusight", "is_demo_label": False},
                )
            )
    db.flush()


def import_observation_frame(db: Session, rows: pd.DataFrame, *, filename: str, file_hash: str) -> dict:
    ensure_research_entities(db, rows)
    batch_id = f"flusight-{uuid4().hex[:10]}"
    db.add(
        ImportBatch(
            id=batch_id,
            source_id=SOURCE_ID,
            file_hash=file_hash,
            filename=filename,
            imported_at=utcnow(),
            row_count=int(len(rows)),
            note="FluSight vintage 导入。原始版本追加保存。",
            is_synthetic=False,
        )
    )
    db.flush()
    records = []
    for row in rows.itertuples(index=False):
        value = row.value
        try:
            number = float(value)
            if number != number:
                number = None
        except (TypeError, ValueError):
            number = None
        records.append(
            {
                "disease_id": DISEASE_ID,
                "region_id": str(row.region_id),
                "period_start": pd.Timestamp(row.period_start).to_pydatetime(),
                "period_end": pd.Timestamp(row.period_end).to_pydatetime(),
                "issued_at": pd.Timestamp(row.issued_at).to_pydatetime(),
                "metric": "hospital_admissions",
                "value": number,
                "unit": "count",
                "source_id": SOURCE_ID,
                "quality_flag": str(getattr(row, "quality_flag", "observed") or "observed"),
                "import_batch_id": batch_id,
            }
        )
    if records:
        db.bulk_insert_mappings(ObservationVintage, records)
    db.commit()
    return {"batch_id": batch_id, "row_count": len(records)}


def _forecast_rows(export: dict) -> list[dict]:
    rows = []
    evals = {(item["model_id"], item["region_id"], item["target_period_end"], item["horizon"]): item for item in export.get("evaluations") or []}
    for row in export.get("forecasts") or []:
        key = (row["model_id"], row["region_id"], row["target_period_end"], row["horizon"])
        scored = evals.get(key) or {}
        item = dict(row)
        item["actual_value"] = scored.get("actual_value")
        rows.append(item)
    return rows


def run_vintage_replay(
    *,
    kind: str = "vintage",
    file: Path | None = None,
    locations: list[str] | tuple[str, ...] | None = None,
    models: list[str] | tuple[str, ...] | None = None,
    seed: int = 42,
    step_weeks: int = 2,
    min_train_weeks: int = 26,
    max_origins: int | None = None,
    include_baselines: bool = True,
    baseline_dates: list[str] | None = None,
    cache_dir: Path | None = None,
) -> dict:
    locations = tuple(locations or DEFAULT_RESEARCH_LOCATIONS)
    models = list(models or RESEARCH_ARMS)
    parsed = fetch_flusight_target(
        kind=kind,
        payload=None if file is None else Path(file).read_bytes(),
        locations=locations,
        cache_dir=cache_dir,
    )
    if parsed.get("status") != "success":
        run = start_run({"command": "vintage-replay", "kind": kind, "mode": "research"}, input_hash="unavailable", seed=seed)
        write_json(Path(run["directory"]), "result.json", incomplete_artifacts(parsed.get("message") or "数据不可用"))
        return {**parsed, "run": run}
    rows = observations_from_flusight(parsed)
    rows = subset_observations(rows, locations=locations)
    if rows.empty:
        run = start_run({"command": "vintage-replay", "kind": kind, "mode": "research"}, input_hash="unavailable", seed=seed)
        write_json(Path(run["directory"]), "result.json", incomplete_artifacts("子集为空，不虚构回放成绩。"))
        return {"status": "error", "message": "子集为空。", "run": run}
    splits = infer_splits(rows)
    origins = flu_season_origins(rows["period_end"], min_train_weeks=min_train_weeks, step_weeks=step_weeks)
    if max_origins is not None:
        origins = origins[: int(max_origins)]
    config = {
        "command": "vintage-replay",
        "mode": "research",
        "kind": kind,
        "disease_id": DISEASE_ID,
        "research_question": RESEARCH_QUESTION,
        "replay_mode": parsed.get("replay_mode"),
        "locations": list(locations),
        "models": models,
        "splits": splits.get("splits"),
        "allow_demo_analogs": False,
        "as_of_logs": parsed.get("logs"),
        "n_as_of_after_break": parsed.get("n_as_of_after_break"),
        "step_weeks": step_weeks,
        "min_train_weeks": min_train_weeks,
        "skip_months": [6, 7, 8, 9],
        "change_labels": "none",
    }
    run = start_run(config, input_hash=hash_observations(rows), seed=seed)
    run_dir = Path(run["directory"])
    write_json(run_dir, "inventory.json", {"n_rows": int(len(rows)), "n_regions": int(rows["region_id"].nunique()), "locations": list(locations)})
    write_json(run_dir, "splits.json", splits)
    write_json(run_dir, "provenance.json", parsed.get("provenance") or {})
    write_json(run_dir, "origins.json", {"origins": [item.isoformat() for item in origins]})
    if not origins:
        result = incomplete_artifacts("训练周数不足，未生成回放原点。不写成绩。")
        write_json(run_dir, "result.json", result)
        return {"status": "incomplete", "run": run, **result}
    if splits.get("status") == "degraded" and "locked_test" not in (splits.get("splits") or {}):
        write_text(run_dir, "LIMITS.md", "可见跨度不足，本运行不能作为锁定测试成绩。")
    db_path = run_dir / "research.db"
    db = research_session(db_path)
    try:
        imported = import_observation_frame(
            db,
            rows,
            filename=str((parsed.get("provenance") or {}).get("raw_path") or file or kind),
            file_hash=(parsed.get("provenance") or {}).get("sha256") or hash_observations(rows),
        )
        cutoff = origins[-1]
        experiment = create_experiment(
            db,
            {
                "id": f"flusight-{run['run_id'][-8:]}",
                "name": f"FluSight {kind} 回放",
                "disease_id": DISEASE_ID,
                "region_ids": sorted(set(rows["region_id"].astype(str))),
                "target_metric": "hospital_admissions",
                "cutoff_time": cutoff.to_pydatetime(),
                "horizons": [1, 2, 3, 4],
                "models": models,
                "replay_mode": "vintage" if kind == "vintage" else "final_time_split",
                "maturity_lag_days": 28,
                "random_seed": int(seed),
                "mode": "research",
                "config": config,
                "origins": [item.isoformat() for item in origins],
            },
        )
        completed = run_experiment(db, experiment.id)
        exported = export_experiment(db, experiment.id)
        write_json(run_dir, "export.json", exported)
        scores = (completed.summary or {}).get("models") or {}
        write_json(run_dir, "scores.json", scores)
        obs = observations_frame(db, disease_id=DISEASE_ID)
        development = (splits.get("splits") or {}).get("development")
        thresholds = {}
        if development:
            dev_rows = obs[
                (pd.to_datetime(obs["period_end"]) >= pd.to_datetime(development["start"]))
                & (pd.to_datetime(obs["period_end"]) <= pd.to_datetime(development["end"]))
            ]
            thresholds = development_thresholds(dev_rows)
        write_json(run_dir, "thresholds.json", {"thresholds": thresholds, "source": "development_quantile_0.90"})
        joint = evaluate_joint_alerts(
            _forecast_rows(exported),
            thresholds=thresholds,
            seed=int(seed),
        )
        write_json(run_dir, "joint_alerts.json", joint)
        change = diagnosis_report([], None, source="none", mode="research")
        write_json(run_dir, "change_diagnosis.json", change)
        baselines = {"status": "skipped", "message": "未请求外部提交对照。"}
        if include_baselines:
            dates = baseline_dates
            if not dates:
                listed = list_hub_reference_dates("FluSight-baseline")
                origin_saturdays = {
                    (as_timestamp(item).normalize() + pd.Timedelta(days=(5 - int(as_timestamp(item).weekday())) % 7)).date().isoformat()
                    for item in origins
                }
                hub_dates = listed.get("dates") or []
                dates = [item for item in hub_dates if item in origin_saturdays][-8:]
                if len(dates) < 3:
                    dates = hub_dates[-6:]
            baselines = fetch_and_score_baselines(
                reference_dates=dates,
                observations=obs,
                as_of=cutoff,
                maturity_lag_days=28,
            )
        write_json(run_dir, "baselines.json", baselines)
        summary = {
            "status": completed.status,
            "experiment_id": experiment.id,
            "n_origins": len(origins),
            "n_rows": int(len(rows)),
            "n_regions": int(rows["region_id"].nunique()),
            "n_scored": (completed.summary or {}).get("n_scored"),
            "models": scores,
            "splits": splits,
            "joint_alerts": {"status": joint.get("status"), "brier": joint.get("brier"), "methods": joint.get("methods"), "degraded_share": joint.get("degraded_share")},
            "change_diagnosis": change,
            "baselines": {"status": baselines.get("status"), "by_model": baselines.get("by_model"), "message": baselines.get("message")},
            "paper_conclusion": None,
            "note": "本运行不写论文结论。本地运行时间不能证明独立公开发布。无真实变化类型标签，不报告变化诊断准确率。",
            "imported": imported,
        }
        write_json(run_dir, "result.json", summary)
        write_text(
            run_dir,
            "README.md",
            "\n".join(
                [
                    f"# research run {run['run_id']}",
                    "",
                    RESEARCH_QUESTION,
                    "",
                    f"- replay_mode: {parsed.get('replay_mode')}",
                    f"- origins: {len(origins)}",
                    f"- scored: {(completed.summary or {}).get('n_scored')}",
                    f"- joint alerts: {joint.get('methods')}",
                    f"- baselines: {baselines.get('status')}",
                    f"- change diagnosis: {change.get('status')}",
                    "",
                    "没有完成评分时不写论文结论。本地归档时间戳不能证明独立公开发布。",
                ]
            ),
        )
        return {"status": "success" if completed.status == "completed" else completed.status, "run": run, "result": summary}
    finally:
        db.close()
