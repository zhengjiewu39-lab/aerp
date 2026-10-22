from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np

from aerp.core.context import context_similarity

ANALOG_DISCLAIMER = (
    "历史形态是根据公开叙述归纳的归一化曲线，不是可训练的真实周住院序列，也不含编造的病例数或 R0。"
    "类比只调整短期方向与权重，不能当作跨疾病迁移学习已经完成。"
)

MIN_MATCH_WINDOW = 8
DEFAULT_WINDOW = 20
SHAPE_WEIGHT = 0.75
CONTEXT_WEIGHT = 0.25


def _normalize(values: np.ndarray) -> np.ndarray:
    series = np.asarray(values, dtype=float)
    series = np.maximum(series, 0.0)
    peak = float(np.max(series)) if series.size else 0.0
    if peak <= 1e-9:
        return np.zeros_like(series)
    return series / peak


def _minmax(values: np.ndarray) -> np.ndarray:
    lo = float(np.min(values))
    hi = float(np.max(values))
    if hi - lo <= 1e-9:
        return np.zeros_like(values)
    return (values - lo) / (hi - lo)


def _cosine(left: np.ndarray, right: np.ndarray) -> float:
    denom = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denom <= 1e-12:
        return 0.0
    return float(np.clip(np.dot(left, right) / denom, -1.0, 1.0))


def _seasonal(n: int = 104) -> np.ndarray:
    week = np.arange(n)
    wave = 0.22 + 0.78 * (0.5 + 0.5 * np.sin(2 * np.pi * (week - 12) / 52.0)) ** 1.7
    return _normalize(wave)


def _unimodal(n: int, peak: float, sigma: float) -> np.ndarray:
    week = np.arange(n)
    return _normalize(np.exp(-0.5 * ((week - peak) / sigma) ** 2))


def _two_wave(n: int = 88) -> np.ndarray:
    week = np.arange(n)
    first = np.exp(-0.5 * ((week - 16) / 5.5) ** 2)
    second = 0.78 * np.exp(-0.5 * ((week - 52) / 8.0) ** 2)
    return _normalize(first + second)


def _multi_wave(n: int = 96) -> np.ndarray:
    week = np.arange(n)
    waves = (
        np.exp(-0.5 * ((week - 14) / 5.0) ** 2)
        + 0.85 * np.exp(-0.5 * ((week - 40) / 6.0) ** 2)
        + 0.7 * np.exp(-0.5 * ((week - 72) / 7.0) ** 2)
    )
    return _normalize(waves)


def _exponential_then_dampen(n: int = 64) -> np.ndarray:
    week = np.arange(n)
    growth = np.exp(0.11 * np.minimum(week, 26))
    decline = growth[26] * np.exp(-0.045 * np.maximum(week - 26, 0))
    values = np.where(week <= 26, growth, decline)
    return _normalize(values)


def _reporting_jump(n: int = 64) -> np.ndarray:
    values = np.full(n, 0.32, dtype=float)
    values[28:] = 0.86
    noise = 0.04 * np.sin(2 * np.pi * np.arange(n) / 10.0)
    return _normalize(np.maximum(values + noise, 0.02))


@lru_cache(maxsize=1)
def analog_library() -> list[dict[str, Any]]:
    """定性形态库。曲线经峰值归一化，禁止当作真实病例数。"""
    items = [
        {
            "id": "seasonal_winter_respiratory",
            "name": "冬春季呼吸道季节波",
            "history_card_id": None,
            "pattern": "seasonal",
            "typical_context": {"climate_zone": "temperate", "setting": "urban"},
            "shape": _seasonal().tolist(),
            "summary": "温带城市常见的年内季节起伏，峰值相对平缓。",
            "comparison_limit": "只比较相对起伏，不对应具体病原体住院数。",
        },
        {
            "id": "smallpox_unimodal_control",
            "name": "天花：单峰后因控制消退",
            "history_card_id": "smallpox",
            "pattern": "unimodal_control",
            "typical_context": {"setting": "mixed", "healthcare_capacity": "medium"},
            "shape": _unimodal(56, peak=22, sigma=8.5).tolist(),
            "summary": "历史叙述中的单峰爆发，随后因隔离或免疫规划迅速回落。",
            "comparison_limit": "根除后的现代监测与历史叙述字段不同，不可混训。",
        },
        {
            "id": "plague_multi_wave",
            "name": "鼠疫：多波次起伏",
            "history_card_id": "plague",
            "pattern": "multi_wave",
            "typical_context": {"setting": "urban", "healthcare_capacity": "low"},
            "shape": _multi_wave().tolist(),
            "summary": "多次历史大流行叙述中的多波次，而不是单一指数增长。",
            "comparison_limit": "中世纪估计与现代周住院修订版本不可直接对齐。",
        },
        {
            "id": "sars_short_unimodal",
            "name": "SARS：短单峰后快速下降",
            "history_card_id": "sars",
            "pattern": "short_unimodal",
            "typical_context": {"setting": "megacity", "healthcare_capacity": "high"},
            "shape": _unimodal(36, peak=12, sigma=4.2).tolist(),
            "summary": "较短的上升-峰值-下降，常与隔离和院感控制同时出现。",
            "comparison_limit": "事件汇总粒度粗于本系统的周住院版本。",
        },
        {
            "id": "covid_two_wave_seasonal",
            "name": "新冠：首波后季节性再起",
            "history_card_id": "covid19",
            "pattern": "two_wave",
            "typical_context": {"climate_zone": "temperate", "setting": "megacity", "mobility": "high"},
            "shape": _two_wave().tolist(),
            "summary": "首波后间隔数月再现第二波，第二波可带季节色彩。",
            "comparison_limit": "未验证外部连接器前，不把该形态当作真实新冠住院训练数据。",
        },
        {
            "id": "true_growth_then_dampen",
            "name": "真实传播增强后阻尼回落",
            "history_card_id": None,
            "pattern": "growth_dampen",
            "typical_context": {"setting": "megacity", "mobility": "high"},
            "shape": _exponential_then_dampen().tolist(),
            "summary": "用于对照“报告跳升”：潜在强度先升，随后增长受上限约束。",
            "comparison_limit": "形态来自演示生成过程，不是某次真实大流行的住院提取。",
        },
        {
            "id": "reporting_jump_not_growth",
            "name": "报告过程跳升（非传播增强）",
            "history_card_id": None,
            "pattern": "reporting_jump",
            "typical_context": {"healthcare_capacity": "medium"},
            "shape": _reporting_jump().tolist(),
            "summary": "水平突然抬升后走平，更接近检出或报告规则变化。",
            "comparison_limit": "若匹配到该形态，应优先检查修订与检测量，而不是立刻加大传播外推。",
        },
    ]
    for item in items:
        item["disclaimer"] = ANALOG_DISCLAIMER
        item["trainable_series"] = False
        item["n_weeks"] = len(item["shape"])
    return items


def analog_by_id(analog_id: str) -> dict[str, Any] | None:
    for item in analog_library():
        if item["id"] == analog_id:
            return item
    return None


def analog_summaries() -> list[dict[str, Any]]:
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "history_card_id": item["history_card_id"],
            "pattern": item["pattern"],
            "typical_context": item["typical_context"],
            "shape": item["shape"],
            "summary": item["summary"],
            "comparison_limit": item["comparison_limit"],
            "trainable_series": False,
            "n_weeks": item["n_weeks"],
            "disclaimer": ANALOG_DISCLAIMER,
        }
        for item in analog_library()
    ]


def recent_window(values: np.ndarray, window: int = DEFAULT_WINDOW) -> np.ndarray | None:
    series = np.asarray(values, dtype=float)
    finite = np.where(np.isfinite(series))[0]
    if finite.size < MIN_MATCH_WINDOW:
        return None
    end = int(finite[-1]) + 1
    start = max(0, end - int(window))
    chunk = series[start:end]
    good = np.isfinite(chunk)
    if int(good.sum()) < MIN_MATCH_WINDOW:
        return None
    index = np.arange(chunk.size)
    filled = np.interp(index, index[good], chunk[good])
    return filled.astype(float)


def _phase(template: np.ndarray, position: int) -> tuple[str, str]:
    last = min(len(template) - 1, position)
    future_end = min(len(template), last + 5)
    future = template[last:future_end]
    level = float(template[last])
    slope = float(future[-1] - template[last]) if future.size else 0.0
    if last + 10 < len(template):
        ahead = template[last : last + 12]
        if level < 0.55 and float(ahead[-1] - ahead[0]) > 0.22:
            return "second_wave", "growth"
    if level > 0.8 and abs(slope) < 0.08:
        return "peak", "peak"
    if slope > 0.08:
        return ("early_growth" if level < 0.4 else "acceleration"), "growth"
    if slope < -0.08:
        return "decline", "decline"
    if level < 0.22:
        return "trough", "stable"
    return "stable_seasonal", "stable"


def _continuation(template: np.ndarray, position: int, level: float, horizons: list[int]) -> list[float]:
    last = min(len(template) - 1, max(0, position))
    current = float(template[last])
    points = []
    for horizon in horizons:
        index = min(len(template) - 1, last + int(horizon))
        ratio = (float(template[index]) + 0.05) / (current + 0.05)
        points.append(max(0.0, float(level) * ratio))
    return points


def _scan_template(query: np.ndarray, template: np.ndarray) -> tuple[int, float]:
    length = len(query)
    if len(template) < length:
        return 0, _cosine(_minmax(query), _minmax(template[:length] if template.size else query))
    best_score = -1.0
    best_pos = 0
    shaped = _minmax(query)
    for start in range(0, len(template) - length + 1):
        score = _cosine(shaped, _minmax(template[start : start + length]))
        if score > best_score:
            best_score = score
            best_pos = start + length - 1
    return best_pos, float(best_score)


@dataclass
class AnalogMatch:
    analog_id: str
    name: str
    source: str
    history_card_id: str | None
    pattern: str
    shape_score: float
    context_score: float
    combined_score: float
    phase: str
    direction: str
    position: int
    continuation: list[float]
    aligned_shape: list[float]
    comparison_limit: str
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "analog_id": self.analog_id,
            "name": self.name,
            "source": self.source,
            "history_card_id": self.history_card_id,
            "pattern": self.pattern,
            "shape_score": round(self.shape_score, 4),
            "context_score": round(self.context_score, 4),
            "combined_score": round(self.combined_score, 4),
            "phase": self.phase,
            "direction": self.direction,
            "position": self.position,
            "continuation": [round(item, 4) for item in self.continuation],
            "aligned_shape": [round(item, 4) for item in self.aligned_shape],
            "comparison_limit": self.comparison_limit,
            "notes": self.notes,
        }


def match_values(
    values: np.ndarray,
    *,
    context: dict | None = None,
    peers: dict[str, dict] | None = None,
    window: int = DEFAULT_WINDOW,
    horizons: list[int] | None = None,
    min_score: float = 0.0,
) -> dict[str, Any]:
    horizons = [int(item) for item in (horizons or [1, 2, 3, 4])]
    query = recent_window(values, window)
    if query is None:
        return {
            "status": "degraded",
            "message": "可见周数不足，无法做历史形态匹配。缺失未记为 0。",
            "disclaimer": ANALOG_DISCLAIMER,
            "query_shape": [],
            "matches": [],
            "top": None,
        }
    finite = np.where(np.isfinite(np.asarray(values, dtype=float)))[0]
    level = float(values[int(finite[-1])])
    matches: list[AnalogMatch] = []
    for item in analog_library():
        template = np.asarray(item["shape"], dtype=float)
        position, shape_score = _scan_template(query, template)
        context_score = context_similarity(context, item.get("typical_context"))
        combined = SHAPE_WEIGHT * max(shape_score, 0.0) + CONTEXT_WEIGHT * context_score
        if combined < min_score:
            continue
        start = max(0, position - len(query) + 1)
        aligned = template[start : start + len(query)].tolist()
        phase, direction = _phase(template, position)
        matches.append(
            AnalogMatch(
                analog_id=item["id"],
                name=item["name"],
                source="history_shape",
                history_card_id=item.get("history_card_id"),
                pattern=item["pattern"],
                shape_score=float(shape_score),
                context_score=float(context_score),
                combined_score=float(combined),
                phase=phase,
                direction=direction,
                position=int(position),
                continuation=_continuation(template, position, level, horizons),
                aligned_shape=aligned,
                comparison_limit=item["comparison_limit"],
                notes=[item["summary"]],
            )
        )
    for peer_id, spec in (peers or {}).items():
        peer_values = np.asarray(spec.get("values"), dtype=float)
        peer_query_template = peer_values[np.isfinite(peer_values)]
        if peer_query_template.size < len(query) + 4:
            continue
        position, shape_score = _scan_template(query, _normalize(peer_query_template))
        template = _normalize(peer_query_template)
        context_score = context_similarity(context, spec.get("context"))
        combined = SHAPE_WEIGHT * max(shape_score, 0.0) + CONTEXT_WEIGHT * context_score
        phase, direction = _phase(template, position)
        start = max(0, position - len(query) + 1)
        matches.append(
            AnalogMatch(
                analog_id=f"peer:{peer_id}",
                name=spec.get("name") or f"对照地区 {peer_id}",
                source="peer_region",
                history_card_id=None,
                pattern="peer_leadlag",
                shape_score=float(shape_score),
                context_score=float(context_score),
                combined_score=float(combined),
                phase=phase,
                direction=direction,
                position=int(position),
                continuation=_continuation(template, position, level, horizons),
                aligned_shape=template[start : start + len(query)].tolist(),
                comparison_limit="只使用截止时间之前的对照地区序列。相似不等于同一传播机制。",
                notes=["同时期其他地区的相位对照，不是未来数据。"],
            )
        )
    matches.sort(key=lambda item: item.combined_score, reverse=True)
    top = matches[0] if matches else None
    message = "已完成形态匹配。" if top else "没有可用形态。"
    if top and top.combined_score < 0.55:
        message = "最佳形态相似度偏低，类比只作为弱提示，不主导预测。"
    return {
        "status": "success" if top else "degraded",
        "message": message,
        "disclaimer": ANALOG_DISCLAIMER,
        "query_shape": _minmax(query).tolist(),
        "level": level,
        "window": int(window),
        "matches": [item.as_dict() for item in matches[:6]],
        "top": None if top is None else top.as_dict(),
    }


def analog_blend_weight(score: float | None, min_score: float = 0.55, max_weight: float = 0.28) -> float:
    if score is None or score < min_score:
        return 0.0
    span = max(1e-6, 1.0 - min_score)
    return float(min(max_weight, 0.06 + max_weight * (score - min_score) / span))


def apply_analog_to_decision(decision, match: dict | None) -> None:
    if not match:
        decision.notes.append("历史形态匹配不足，不调整预测方向。")
        return
    score = float(match.get("combined_score") or 0.0)
    direction = match.get("direction")
    pattern = match.get("pattern")
    name = match.get("name")
    if score < 0.55:
        decision.notes.append(f"与「{name}」形态接近但分数偏低（{score:.2f}），仅记录对照，不改变窗口。")
        return
    decision.notes.append(f"当前序列更接近「{name}」，阶段 {match.get('phase')}，方向 {direction}。")
    if pattern == "reporting_jump":
        decision.use_revision_adjustment = True
        decision.notes.append("形态更像报告跳升：优先检查修订，而不是加大传播外推。")
        if "retrain_glm" not in decision.downweight_models:
            decision.downweight_models.append("retrain_glm")
    if direction == "growth":
        decision.compare_windows = True
        decision.notes.append("类比指向继续上升：比较短/长训练窗口。")
    if direction in {"peak", "second_wave"} or match.get("phase") in {"peak", "second_wave"}:
        decision.interval_scale = max(float(decision.interval_scale), 1.2)
        decision.notes.append("类比指向峰值或第二波：扩大短期区间，不保证名义覆盖率。")
    if direction == "decline" and "retrain_glm" not in decision.downweight_models:
        decision.downweight_models.append("retrain_glm")
        decision.notes.append("类比指向回落：降低仍外推上升的计数回归权重。")
