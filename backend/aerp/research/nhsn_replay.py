"""按病种导入 Delphi NHSN 周住院并跑小范围回放。"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pandas as pd

from aerp.core.diseases import get_disease
from aerp.core.hashing import hash_observations
from aerp.core.timeutil import as_timestamp, utcnow
from aerp.db.models import DataSource, Disease, ImportBatch, ObservationVintage, Region
from aerp.research.dbutil import research_session
from aerp.research.runs import start_run, write_json
from aerp.research.splits import infer_splits
from aerp.services.delphi import SIGNAL_BY_DISEASE, fetch_nhsn_weekly, observations_from_delphi
from aerp.services.experiment_engine import create_experiment, export_experiment, run_experiment
from aerp.services.seed import ensure_disease_registry


def _ensure_entities(db, rows: pd.DataFrame, *, disease_id: str, source_id: str) -> None:
    ensure_disease_registry(db)
    db.flush()
    spec = get_disease(disease_id)
    row = db.get(Disease, disease_id)
    if row is None:
        db.add(Disease(id=disease_id, name=disease_id, description="", is_synthetic=False))
    elif spec:
        row.is_synthetic = False
    if db.get(DataSource, source_id) is None:
        db.add(
            DataSource(
                id=source_id,
                name=f"Delphi NHSN {disease_id}",
                url="https://api.delphi.cmu.edu/epidata",
                license="Delphi 与 CDC NHSN 条款",
                update_note="独立信号，不是流感连接器改名。",
                connector_status="available",
            )
        )
    for region_id in sorted(set(rows["region_id"].astype(str))):
        if db.get(Region, region_id) is None:
            db.add(
                Region(
                    id=region_id,
                    name=region_id,
                    note=f"{disease_id} NHSN 辖区",
                    context_json={"source": "delphi_nhsn", "is_demo_label": False},
                )
            )
    db.flush()


def run_nhsn_replay(
    disease_id: str,
    *,
    geo_value: str = "pa",
    time_values: str = "202240-202520",
    seed: int = 42,
    max_origins: int = 8,
    payload: bytes | None = None,
) -> dict:
    spec = SIGNAL_BY_DISEASE.get(disease_id)
    if spec is None:
        return {"status": "error", "message": f"{disease_id} 没有 NHSN 周住院信号。"}
    parsed = fetch_nhsn_weekly(disease_id, geo_value=geo_value, time_values=time_values, payload=payload)
    if parsed.get("status") != "success":
        return parsed
    rows = observations_from_delphi(parsed)
    if rows.empty:
        return {"status": "error", "message": "解析成功但没有观测行。"}
    splits = infer_splits(rows, metric=spec["metric"])
    record = start_run(
        {
            "disease_id": disease_id,
            "signal": spec["signal"],
            "geo_value": geo_value,
            "time_values": time_values,
            "max_origins": max_origins,
        },
        input_hash=hash_observations(rows),
        seed=seed,
    )
    run_dir = Path(record["directory"])
    write_json(run_dir, "inventory.json", {"n_rows": int(len(rows)), "n_regions": int(rows["region_id"].nunique()), "provenance": parsed.get("provenance")})
    write_json(run_dir, "splits.json", splits)
    db = research_session(run_dir / "research.db")
    source_id = f"delphi-nhsn-{disease_id}"
    _ensure_entities(db, rows, disease_id=disease_id, source_id=source_id)
    batch_id = f"nhsn-{disease_id}-{uuid4().hex[:8]}"
    db.add(
        ImportBatch(
            id=batch_id,
            source_id=source_id,
            file_hash=(parsed.get("provenance") or {}).get("sha256") or hash_observations(rows),
            filename=(parsed.get("provenance") or {}).get("raw_path") or "",
            imported_at=utcnow(),
            row_count=int(len(rows)),
            note=f"{disease_id} NHSN 导入。downloaded_at 不是历史发布时间。",
            is_synthetic=False,
        )
    )
    db.flush()
    for row in rows.itertuples(index=False):
        db.add(
            ObservationVintage(
                disease_id=str(row.disease_id),
                region_id=str(row.region_id),
                period_start=pd.Timestamp(row.period_start).to_pydatetime(),
                period_end=pd.Timestamp(row.period_end).to_pydatetime(),
                issued_at=pd.Timestamp(row.issued_at).to_pydatetime(),
                metric=str(row.metric),
                value=None if pd.isna(row.value) else float(row.value),
                unit=str(row.unit),
                source_id=source_id,
                quality_flag=str(row.quality_flag),
                import_batch_id=batch_id,
            )
        )
    db.commit()
    ends = sorted(pd.to_datetime(rows["period_end"]).unique())
    if len(ends) < 30:
        db.close()
        result = {
            "status": "incomplete",
            "message": f"{disease_id} 可见周数 {len(ends)}，不足以负责任地做 1–4 周多原点回放。",
            "n_rows": int(len(rows)),
            "run_dir": str(run_dir),
        }
        write_json(run_dir, "result.json", result)
        return result
    cutoff = as_timestamp(ends[-5]).date().isoformat()
    origins = [as_timestamp(item).date().isoformat() for item in ends[24:-4:max(1, len(ends) // max(max_origins, 1))]][:max_origins]
    experiment = create_experiment(
        db,
        {
            "name": f"NHSN {disease_id} 小范围回放",
            "disease_id": disease_id,
            "region_ids": sorted(set(rows["region_id"].astype(str))),
            "target_metric": spec["metric"],
            "cutoff_time": cutoff,
            "horizons": [1, 2, 3, 4],
            "models": ["persistence", "damped_trend", "retrain_glm", "equal_weight"],
            "replay_mode": parsed.get("replay_mode") or "final_time_split",
            "maturity_lag_days": 28,
            "random_seed": seed,
            "mode": "research",
            "origins": origins,
        },
    )
    completed = run_experiment(db, experiment.id)
    exported = export_experiment(db, experiment.id)
    write_json(run_dir, "export.json", exported)
    write_json(run_dir, "scores.json", (completed.summary or {}).get("models") or {})
    write_json(run_dir, "locked_test.json", (completed.summary or {}).get("models_locked_test") or {})
    db.close()
    return {
        "status": completed.status,
        "disease_id": disease_id,
        "run_dir": str(run_dir),
        "n_rows": int(len(rows)),
        "n_origins": len(origins),
        "primary_split": (completed.summary or {}).get("primary_split"),
        "models": (completed.summary or {}).get("models"),
        "models_locked_test": (completed.summary or {}).get("models_locked_test"),
        "replay_mode": parsed.get("replay_mode"),
        "note": "这是单地区小范围闭环，不是全季节全国主结果。"
        + (" 当前文件只有单一 issue，按最终数据时间切分，不能称为真实实时回测。" if parsed.get("replay_mode") != "vintage" else ""),
    }
