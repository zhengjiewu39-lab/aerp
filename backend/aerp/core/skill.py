from __future__ import annotations

import numpy as np
import pandas as pd

from aerp.core.timeutil import as_timestamp

SKILL_DEFINITION = (
    "只在带有标签的地区上计算精确率、召回率和提前量。没有标签时不计算这些指标，也不计算误报率。"
    "提前量（周）=（标签周期开始 − 最早匹配预警的周期开始）/ 7，正值表示预警更早。"
    "匹配要求同一地区、同一分类，且周期开始相差不超过 14 天。"
)

RECOVERY_DEFINITION = (
    "恢复标准：标签周之前最多 8 个已评分的 1 周前预测，其绝对误差均值为基线。"
    "之后若某个目标周及其后连续 3 个已评分目标周的平均绝对误差不超过基线的 1.25 倍，则记为恢复。"
    "恢复周数是首次满足该标准的目标周减去标签周。到评价结束仍未满足则记为未恢复。"
    "该标准和阶段标签只用于事后分组，不参与当时的模型选择。"
)


def _weeks_between(later, earlier) -> float:
    return (as_timestamp(later) - as_timestamp(earlier)).days / 7.0


def alert_skill(alerts: list[dict], labels: pd.DataFrame | None) -> dict | None:
    if labels is None or labels.empty:
        return None
    label_regions = set(labels["region_id"].astype(str))
    considered = [alert for alert in alerts if str(alert.get("region_id")) in label_regions]
    label_rows = labels.to_dict(orient="records")
    matched_alerts = 0
    leads = []
    matched_labels = 0
    for label in label_rows:
        hits = []
        for alert in considered:
            if alert.get("category") != label.get("category"):
                continue
            if str(alert.get("region_id")) != str(label.get("region_id")):
                continue
            if abs((as_timestamp(alert.get("period_start")) - as_timestamp(label.get("period_start"))).days) <= 14:
                hits.append(alert)
        if hits:
            matched_labels += 1
            earliest = min(hits, key=lambda alert: as_timestamp(alert.get("period_start")))
            leads.append(_weeks_between(label.get("period_start"), earliest.get("period_start")))
    for alert in considered:
        if any(
            alert.get("category") == label.get("category")
            and str(alert.get("region_id")) == str(label.get("region_id"))
            and abs((as_timestamp(alert.get("period_start")) - as_timestamp(label.get("period_start"))).days) <= 14
            for label in label_rows
        ):
            matched_alerts += 1
    precision = None if not considered else matched_alerts / len(considered)
    recall = None if not label_rows else matched_labels / len(label_rows)
    return {
        "precision": precision,
        "recall": recall,
        "median_lead_weeks": None if not leads else float(np.median(leads)),
        "n_alerts": len(considered),
        "n_labels": len(label_rows),
        "definition": SKILL_DEFINITION,
    }


def recovery_summary(evaluations: list[dict], labels: pd.DataFrame | None, model_id: str = "adaptive") -> dict | None:
    if labels is None or labels.empty:
        return None
    rows = [
        row
        for row in evaluations
        if row.get("model_id") == model_id and row.get("status") == "scored" and int(row.get("horizon", 0)) == 1 and row.get("abs_error") is not None
    ]
    items = []
    for label in labels.to_dict(orient="records"):
        region_rows = [row for row in rows if row.get("region_id") == label.get("region_id")]
        region_rows.sort(key=lambda row: as_timestamp(row["target_period_end"]))
        label_time = as_timestamp(label["period_start"])
        before = [row for row in region_rows if as_timestamp(row["target_period_end"]) < label_time][-8:]
        after = [row for row in region_rows if as_timestamp(row["target_period_end"]) >= label_time]
        if len(before) < 4 or len(after) < 4:
            items.append({"region_id": label.get("region_id"), "status": "未计算", "reason": "标签前后的已评分 1 周预测不足。"})
            continue
        baseline = float(np.mean([float(row["abs_error"]) for row in before]))
        recovered = None
        for index in range(0, len(after) - 3):
            window = after[index : index + 4]
            if float(np.mean([float(row["abs_error"]) for row in window])) <= 1.25 * baseline:
                recovered = window[0]
                break
        if recovered is None:
            items.append({"region_id": label.get("region_id"), "status": "未恢复", "baseline_mae": baseline})
        else:
            items.append(
                {
                    "region_id": label.get("region_id"),
                    "status": "已恢复",
                    "baseline_mae": baseline,
                    "weeks_to_recovery": _weeks_between(recovered["target_period_end"], label["period_start"]),
                }
            )
    return {"model_id": model_id, "definition": RECOVERY_DEFINITION, "items": items}
