from __future__ import annotations

from typing import Any

RESEARCH_QUESTION = (
    "在报告延迟、数据修订及疫情趋势变化条件下，结合监测变化识别与地区信息共享的自适应方法，"
    "是否比常规更新和组合方法提供更可靠的短期住院预测？"
)

MODE_DEMO = "demo"
MODE_RESEARCH = "research"

RESEARCH_GUARDS = {
    "demo_analogs_in_official_forecasts": False,
    "subjective_context_interval_scale": False,
    "auto_mix_synthetic": False,
    "unverified_connectors_as_available": False,
}


def resolve_mode(config: dict | None, payload: dict | None = None) -> str:
    raw = None
    if payload:
        raw = payload.get("mode") or (payload.get("config") or {}).get("mode")
    if raw is None and config:
        raw = config.get("mode")
    mode = str(raw or MODE_DEMO).strip().lower()
    if mode not in {MODE_DEMO, MODE_RESEARCH}:
        raise ValueError(f"未知运行模式: {mode}")
    return mode


def is_research(config: dict | None, payload: dict | None = None) -> bool:
    return resolve_mode(config, payload) == MODE_RESEARCH


def analog_allowed_in_forecasts(config: dict | None) -> bool:
    if is_research(config):
        return bool((config or {}).get("allow_demo_analogs", False))
    return True


def context_interval_scale_allowed(config: dict | None) -> bool:
    if is_research(config):
        return False
    return True


def research_notes(config: dict | None) -> list[str]:
    if not is_research(config):
        return ["当前为演示模式。合成数据、定性形态库和主观区间倍率可用于教学，不能当作论文证据。"]
    return [
        "研究模式：手工历史形态不进入正式预测，除非显式打开 allow_demo_analogs。",
        "研究模式：不按城市或医疗容量标签设置主观区间倍率。",
        "研究模式：不自动混入合成演示数据。缺少必要输入时失败或降级，不伪造成功。",
        "天花、鼠疫、SARS 等定性曲线仅供教学，不是真实历史病例训练或跨疾病迁移证据。",
        RESEARCH_QUESTION,
    ]


def public_mode_payload(config: dict | None) -> dict[str, Any]:
    mode = resolve_mode(config)
    return {
        "mode": mode,
        "research_question": RESEARCH_QUESTION,
        "guards": dict(RESEARCH_GUARDS) if mode == MODE_RESEARCH else {},
        "notes": research_notes(config),
    }
