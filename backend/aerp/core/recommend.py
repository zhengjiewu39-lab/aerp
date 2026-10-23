"""根据 DiseaseSpec 与数据量推荐候选模型。规则明确，不按病名断言最优。"""

from __future__ import annotations

from aerp.core.diseases import (
    TARGET_WEEKLY_ADMISSIONS,
    TARGET_WEEKLY_CASES,
    TRANSMISSION_RESPIRATORY,
    TRANSMISSION_VECTOR,
    get_disease,
)


def recommend_models(disease_id: str, *, target: str | None = None, n_weeks: int = 0, has_climate: bool = False) -> dict:
    spec = get_disease(disease_id)
    if spec is None:
        return {
            "status": "unknown_disease",
            "candidates": ["persistence"],
            "reasons": ["未知疾病，仅建议持续值基线，并与任何候选比较。"],
            "must_compare_baselines": True,
        }
    target = target or spec.default_target
    candidates: list[str] = ["persistence"]
    reasons: list[str] = ["持续值是强制对照，不因疾病名称删除。"]
    if not spec.trainable:
        planned = ["persistence"]
        if spec.transmission == TRANSMISSION_VECTOR:
            planned.extend(["damped_trend", "retrain_glm"])
        return {
            "status": spec.status,
            "disease_id": disease_id,
            "candidates": planned,
            "reasons": [spec.status_label, spec.uncertainty_limits, "有可靠数据后仍须与基线比较，现在不能训练。"],
            "must_compare_baselines": True,
            "trainable": False,
        }
    if target == TARGET_WEEKLY_ADMISSIONS and spec.transmission == TRANSMISSION_RESPIRATORY:
        candidates.extend(["damped_trend", "retrain_glm", "equal_weight"])
        reasons.append("呼吸道周住院：计数时序与简单组合是默认候选。")
        if n_weeks >= 40:
            candidates.append("hierarchical")
            reasons.append("地区数与周数足够时，才把地区部分共享列为候选，仍需对照统一/独立模型。")
        candidates.append("adaptive")
        reasons.append("适应规则是研究臂，不默认优于等权。")
    elif target == TARGET_WEEKLY_CASES and spec.transmission == TRANSMISSION_VECTOR:
        candidates.extend(["damped_trend", "retrain_glm"])
        reasons.append("登革热周病例：季节性计数模型是候选。")
        if has_climate:
            reasons.append("已声明有气候/媒介数据，才允许对应辅助变量；本版仍不自动断言最优。")
        else:
            reasons.append("无气候和媒介数据，不启用气候辅助变量。")
    elif disease_id == "measles" or (target == TARGET_WEEKLY_CASES and n_weeks < 40):
        candidates.extend(["damped_trend", "retrain_glm"])
        reasons.append("麻疹或稀疏爆发：允许零值与离散性；样本不足时降级到持续值。")
        if n_weeks < 16:
            candidates = ["persistence"]
            reasons.append("可见周数不足 16，只保留基线。")
    else:
        candidates.extend(["damped_trend", "retrain_glm"])
        reasons.append("通用计数时序候选。必须与基线比较。")
    # 去重并保持顺序
    seen = set()
    ordered = []
    for item in candidates:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return {
        "status": spec.status,
        "disease_id": disease_id,
        "target": target,
        "candidates": ordered,
        "reasons": reasons,
        "must_compare_baselines": True,
        "note": "推荐遵循预设规则。不能仅根据疾病名称断言某模型最好。",
    }
