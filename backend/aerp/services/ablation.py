from __future__ import annotations

from copy import deepcopy

from sqlalchemy.orm import Session

from aerp.core.synthetic import suggested_origins
from aerp.core.timeutil import as_timestamp
from aerp.services.experiment_engine import create_experiment, experiment_to_dict, run_experiment
from aerp.services.observations import observations_frame


ABLATION_VARIANTS = (
    {"key": "full", "label": "完整系统", "ablations": {"no_revision": False, "no_anomaly": False, "no_dynamic_weight": False, "no_hierarchy": False, "no_dynamic_update": False}},
    {"key": "no_revision", "label": "删除修订处理", "ablations": {"no_revision": True, "no_anomaly": False, "no_dynamic_weight": False, "no_hierarchy": False, "no_dynamic_update": False}},
    {"key": "no_anomaly", "label": "删除异常检测", "ablations": {"no_revision": False, "no_anomaly": True, "no_dynamic_weight": False, "no_hierarchy": False, "no_dynamic_update": False}},
    {"key": "no_dynamic_weight", "label": "删除动态权重", "ablations": {"no_revision": False, "no_anomaly": False, "no_dynamic_weight": True, "no_hierarchy": False, "no_dynamic_update": False}},
    {"key": "no_hierarchy", "label": "删除适应臂中的地区部分共享", "ablations": {"no_revision": False, "no_anomaly": False, "no_dynamic_weight": False, "no_hierarchy": True, "no_dynamic_update": False}},
    {"key": "no_dynamic_update", "label": "删除动态更新", "ablations": {"no_revision": False, "no_anomaly": False, "no_dynamic_weight": False, "no_hierarchy": False, "no_dynamic_update": True, "no_uncertainty_propagation": False}},
    {"key": "no_uncertainty_propagation", "label": "删除修订不确定性传播", "ablations": {"no_revision": False, "no_anomaly": False, "no_dynamic_weight": False, "no_hierarchy": False, "no_dynamic_update": False, "no_uncertainty_propagation": True}},
)


def _default_origins(db: Session, disease_id: str, cutoff) -> list[str]:
    frame = observations_frame(db, disease_id=disease_id)
    origins = suggested_origins(frame, cutoff, start_week=72, step=12)
    return [as_timestamp(item).date().isoformat() for item in origins]


def run_ablation_suite(db: Session, base_payload: dict) -> dict:
    """同步运行完整系统与三项消融，比较 WIS/MAE。不硬编码胜者。"""
    results = []
    shared_origins = base_payload.get("origins")
    if not shared_origins:
        shared_origins = _default_origins(db, base_payload["disease_id"], base_payload["cutoff_time"])
    for variant in ABLATION_VARIANTS:
        payload = deepcopy(base_payload)
        payload["origins"] = list(shared_origins)
        payload["name"] = f"{base_payload.get('name') or '消融对照'} · {variant['label']}"
        payload["ablations"] = dict(variant["ablations"])
        payload["config"] = {
            **(payload.get("config") or {}),
            "ablation_suite_key": variant["key"],
            "ablation_suite_label": variant["label"],
        }
        experiment = create_experiment(db, payload)
        finished = run_experiment(db, experiment.id)
        models = (finished.summary or {}).get("models") or {}
        adaptive = models.get("adaptive") or {}
        equal = models.get("equal_weight") or {}
        results.append(
            {
                "key": variant["key"],
                "label": variant["label"],
                "experiment_id": finished.id,
                "status": finished.status,
                "error_message": finished.error_message,
                "adaptive_wis": adaptive.get("wis"),
                "adaptive_mae": adaptive.get("mae"),
                "equal_weight_wis": equal.get("wis"),
                "equal_weight_mae": equal.get("mae"),
                "models": {
                    model_id: {"wis": stats.get("wis"), "mae": stats.get("mae"), "n": stats.get("n")}
                    for model_id, stats in models.items()
                },
                "experiment": experiment_to_dict(finished),
            }
        )

    ranked = sorted(
        [row for row in results if row["adaptive_wis"] is not None],
        key=lambda row: float(row["adaptive_wis"]),
    )
    best = ranked[0] if ranked else None
    full = next((row for row in results if row["key"] == "full"), None)
    note = (
        "消融结果由真实回放计算。完整系统不默认优于任一消融或等权对照；"
        "若完整系统 WIS 更高，应如实展示为无改善或更差。"
    )
    comparison = {
        "best_adaptive_by_wis": None
        if best is None
        else {"key": best["key"], "label": best["label"], "adaptive_wis": best["adaptive_wis"]},
        "full_beats_ablations": None,
        "note": note,
    }
    if full and full["adaptive_wis"] is not None and ranked:
        comparison["full_beats_ablations"] = all(
            full["adaptive_wis"] <= float(row["adaptive_wis"])
            for row in ranked
            if row["key"] != "full"
        )
    return {"variants": results, "comparison": comparison}
