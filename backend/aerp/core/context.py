from __future__ import annotations

from typing import Any

CONTEXT_KEYS = (
    "climate_zone",
    "setting",
    "age_structure",
    "healthcare_capacity",
    "mobility",
    "population_density",
)

FIELD_LABELS = {
    "country": "国家/地区组",
    "climate_zone": "气候带",
    "setting": "聚居环境",
    "age_structure": "年龄结构",
    "healthcare_capacity": "医疗容量带",
    "mobility": "流动强度",
    "population_density": "人口密度带",
}

VALUE_LABELS = {
    "temperate": "温带",
    "subtropical": "亚热带",
    "tropical": "热带",
    "arid": "干旱",
    "urban": "城市",
    "megacity": "超大城市",
    "mixed": "城乡混合",
    "rural": "农村",
    "aging": "偏老龄化",
    "young": "偏年轻",
    "balanced": "年龄结构较均衡",
    "high": "高",
    "medium": "中",
    "low": "低",
}

CONTEXT_NOTE = "上下文只用于形态匹配加权、区间倍率与跨地区对照，不是因果证明，也不是真实普查或卫生系统评价。"


def context_similarity(left: dict | None, right: dict | None) -> float:
    """共享标签的一致比例。缺标签时给中性分，不假装完全相似。"""
    if not left or not right:
        return 0.5
    comparable = 0
    matched = 0
    for key in CONTEXT_KEYS:
        a = left.get(key)
        b = right.get(key)
        if not a or not b:
            continue
        comparable += 1
        if a == b:
            matched += 1
    if comparable == 0:
        return 0.5
    return matched / comparable


def interval_scale_for_context(context: dict | None) -> tuple[float, str | None]:
    if not context:
        return 1.0, None
    scale = 1.0
    notes = []
    if context.get("healthcare_capacity") == "low":
        scale = max(scale, 1.15)
        notes.append("医疗容量带为低：略扩大短期区间。")
    if context.get("setting") == "megacity":
        scale = max(scale, 1.12)
        notes.append("超大城市标签：人群混杂更高，略扩大区间。")
    if context.get("mobility") == "high" and context.get("population_density") == "high":
        scale = max(scale, 1.18)
        notes.append("高密度且高流动：短期外推不确定性更大。")
    if not notes:
        return 1.0, None
    return scale, " ".join(notes) + " 倍率需回放覆盖率检验，不保证名义覆盖。"


def public_context(raw: dict | None) -> dict[str, Any]:
    if not raw:
        return {
            "available": False,
            "is_demo_label": False,
            "fields": [],
            "raw": {},
            "profile": {
                "country_id": None,
                "population": None,
                "age_distribution": {},
                "household_size": None,
                "crowding_index": None,
                "age_outcome_available": False,
                "ethnicity_analysis": "skipped_no_data",
                "age_note": "没有分年龄住院数据时，不能声称识别了年龄组间传播关系。",
            },
            "note": "未提供国家/人群/环境标签。系统不会编造这些因素。",
        }
    fields = []
    country = raw.get("country")
    if country:
        fields.append({"key": "country", "label": FIELD_LABELS["country"], "value_id": country, "value": country})
    for key in CONTEXT_KEYS:
        value_id = raw.get(key)
        if not value_id:
            continue
        fields.append(
            {
                "key": key,
                "label": FIELD_LABELS[key],
                "value_id": value_id,
                "value": VALUE_LABELS.get(str(value_id), str(value_id)),
            }
        )
    age = raw.get("age_distribution") if isinstance(raw.get("age_distribution"), dict) else {}
    profile = {
        "country_id": raw.get("country_id"),
        "population": raw.get("population"),
        "age_distribution": age,
        "household_size": raw.get("household_size"),
        "crowding_index": raw.get("crowding_index"),
        "age_outcome_available": False,
        "ethnicity_analysis": raw.get("ethnicity_analysis") or "skipped_no_data",
        "age_note": "只有年龄人口比例、没有分年龄住院时，年龄结构只作地区协变量，不能识别年龄组间传播。",
    }
    return {
        "available": True,
        "is_demo_label": bool(raw.get("is_demo_label")),
        "fields": fields,
        "raw": {key: raw.get(key) for key in ("country", "country_id") + CONTEXT_KEYS if raw.get(key)},
        "profile": profile,
        "note": raw.get("note") or CONTEXT_NOTE,
    }
