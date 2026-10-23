"""预测可靠性证据。各项单独展示，不合成未经校准的准确率。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _share_complete(frame: pd.DataFrame) -> float | None:
    if frame is None or frame.empty:
        return None
    values = pd.to_numeric(frame.get("value"), errors="coerce")
    if values.empty:
        return None
    return float(np.mean(np.isfinite(values.to_numpy(dtype=float))))


def reliability_evidence(
    *,
    train_frame: pd.DataFrame | None = None,
    revision: dict | None = None,
    recent_scores: list[dict] | None = None,
    predictions: list[dict] | None = None,
    used_future_covariates: bool = False,
    transfer_exit: dict | None = None,
    degraded: bool = False,
    current_level: float | None = None,
) -> dict[str, Any]:
    complete = _share_complete(train_frame)
    n_train = 0 if train_frame is None or train_frame.empty else int(train_frame["value"].notna().sum()) if "value" in train_frame.columns else int(len(train_frame))
    revision = revision or {}
    scores = [row for row in (recent_scores or []) if row.get("status") == "scored" and row.get("covered_80") is not None]
    coverage = None if not scores else float(np.mean([float(row["covered_80"]) for row in scores]))
    medians = []
    for item in predictions or []:
        quantiles = item.get("quantiles") or {}
        if 0.5 in quantiles:
            medians.append(float(quantiles[0.5]))
        elif item.get("median") is not None:
            medians.append(float(item["median"]))
    disagreement = None
    if len(medians) >= 2:
        disagreement = float(np.std(medians) / max(np.mean(np.abs(medians)), 1.0))
    train_max = None
    if train_frame is not None and not train_frame.empty and "value" in train_frame.columns:
        vals = pd.to_numeric(train_frame["value"], errors="coerce").dropna()
        if not vals.empty:
            train_max = float(vals.max())
    out_of_range = None
    if current_level is not None and train_max is not None and train_max > 0:
        out_of_range = bool(current_level > 1.5 * train_max)
    actions = []
    if degraded or (complete is not None and complete < 0.7) or n_train < 16:
        actions.append("fallback_baseline")
    if transfer_exit and transfer_exit.get("exited"):
        actions.append("pause_transfer")
    if coverage is None or (revision and not revision.get("estimable")):
        actions.append("mark_insufficient_evidence")
    return {
        "data_completeness": {
            "n_train": n_train,
            "finite_share": complete,
            "note": "缺失保持为空，不等于 0。",
        },
        "revision_history": {
            "estimable": bool(revision.get("estimable")),
            "n_pairs": revision.get("n_pairs"),
            "message": revision.get("message") or "修订历史不足或未估计。",
        },
        "recent_coverage": {
            "coverage_80": coverage,
            "n": len(scores),
            "note": "仅用已成熟预测。样本不足时为暂无。",
        },
        "out_of_range": {
            "flag": out_of_range,
            "current_level": current_level,
            "train_max": train_max,
        },
        "model_disagreement": {
            "relative_std": disagreement,
            "n_models": len(medians),
        },
        "future_covariates": {
            "used_unknown_future": bool(used_future_covariates),
            "note": "默认不使用未知未来协变量。",
        },
        "degraded_or_exit": {
            "degraded": bool(degraded),
            "transfer_exit": transfer_exit or {"exited": False},
            "actions": actions,
        },
        "composite_accuracy_percent": None,
        "note": "各项单独展示，不加权成准确率百分比。被暂缓任务仍计入完成率。",
    }
