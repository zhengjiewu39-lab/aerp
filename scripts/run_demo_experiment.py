#!/usr/bin/env python3
"""创建并同步运行一次示例实验，结果写入本地 SQLite 与 examples/。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("AERP_SYNC_TASKS", "true")

from aerp.db.session import SessionLocal, init_db
from aerp.services.experiment_engine import create_experiment, export_experiment, run_experiment
from aerp.services.observations import overview
from aerp.services.seed import ensure_seeded


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        ensure_seeded(db)
        ov = overview(db, "synth-ari", "R1")
        cutoff = ov["quality"]["cutoff_time"]
        experiment = create_experiment(
            db,
            {
                "name": "示例实验-合成演示",
                "disease_id": "synth-ari",
                "region_ids": ["R1", "R2", "R3", "R4", "R5"],
                "target_metric": "hospital_admissions",
                "replay_mode": "vintage",
                "cutoff_time": cutoff,
                "maturity_lag_days": 28,
                "train_window_weeks": 52,
                "horizons": [1, 2, 3, 4],
                "random_seed": 42,
                "holdout_region_ids": ["R5"],
                "models": [
                    "persistence",
                    "fixed_glm",
                    "retrain_glm",
                    "pooled_glm",
                    "hierarchical",
                    "hierarchical_dynamic",
                    "equal_weight",
                    "dynamic_weight",
                    "adaptive",
                    "analog",
                ],
                "ablations": {"no_revision": False, "no_anomaly": False, "no_dynamic_weight": False},
            },
        )
        finished = run_experiment(db, experiment.id)
        payload = export_experiment(db, finished.id)
        out = ROOT / "examples" / "sample_experiment.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"status={finished.status}")
        print(f"experiment_id={finished.id}")
        print(f"export={out}")
        models = (finished.summary or {}).get("models") or {}
        for model_id, stats in models.items():
            print(f"wis[{model_id}]={stats.get('wis')} mae={stats.get('mae')} n={stats.get('n')}")
        if finished.status != "completed":
            print(finished.error_message)
            raise SystemExit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
