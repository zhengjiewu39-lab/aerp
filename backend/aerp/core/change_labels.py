from __future__ import annotations

import pandas as pd

from aerp.core.skill import SKILL_DEFINITION, alert_skill

NO_REAL_LABEL_NOTE = (
    "FluSight / NHSN 住院序列没有可信的真实“变化类型”标签（报告过程变化 vs 疫情趋势变化）。"
    "没有这类标签时不报告变化诊断准确率、精确率或召回率。"
)

SYNTHETIC_LABEL_NOTE = (
    "当前标签来自合成演示或人为指定规则，只能单独报告，不能当作真实监测系统的变化诊断准确率。"
)

MANUAL_LABEL_NOTE = (
    "当前标签为用户提供的人工标注。准确率只对这批标注有效，不外推到未标注季节或地区。"
)


def infer_label_source(labels: pd.DataFrame | None, *, declared: str | None = None) -> str:
    if declared in {"none", "synthetic", "manual"}:
        return declared
    if labels is None or labels.empty:
        return "none"
    notes = " ".join(str(item) for item in labels.get("note", pd.Series(dtype=str)).astype(str).tolist())
    categories = " ".join(str(item) for item in labels.get("category", pd.Series(dtype=str)).astype(str).tolist())
    blob = (notes + " " + categories).lower()
    if "synthetic" in blob or "合成" in blob or "demo" in blob:
        return "synthetic"
    if "manual" in blob or "人工" in blob:
        return "manual"
    return "synthetic"


def diagnosis_report(
    alerts: list[dict] | None,
    labels: pd.DataFrame | None,
    *,
    source: str | None = None,
    mode: str = "research",
) -> dict:
    """变化诊断准确率门控：无真实标签不写准确率。"""
    resolved = infer_label_source(labels, declared=source)
    base = {
        "status": "skipped",
        "accuracy": None,
        "precision": None,
        "recall": None,
        "label_source": resolved,
        "mode": mode,
        "definition": SKILL_DEFINITION,
    }
    if resolved == "none" or labels is None or labels.empty:
        return {
            **base,
            "status": "skipped",
            "reason": NO_REAL_LABEL_NOTE,
        }
    skill = alert_skill(alerts or [], labels)
    metrics = {
        "precision": None if skill is None else skill.get("precision"),
        "recall": None if skill is None else skill.get("recall"),
        "median_lead_weeks": None if skill is None else skill.get("median_lead_weeks"),
        "n_alerts": 0 if skill is None else skill.get("n_alerts"),
        "n_labels": 0 if skill is None else skill.get("n_labels"),
    }
    if resolved == "synthetic":
        return {
            **base,
            "status": "synthetic_only",
            "reason": SYNTHETIC_LABEL_NOTE,
            "synthetic_metrics": metrics,
            "note": "synthetic_metrics 不得写入论文真实准确率栏。",
        }
    return {
        **base,
        "status": "manual_labels",
        "reason": MANUAL_LABEL_NOTE,
        "manual_metrics": metrics,
        "accuracy": metrics.get("recall"),
    }
