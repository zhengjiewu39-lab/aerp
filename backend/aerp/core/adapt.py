from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from aerp.core.anomaly import AlertDraft
from aerp.core.models.base import PredictResult
from aerp.core.models.damped import DampedTrendModel
from aerp.core.scoring import score_forecast
from aerp.core.series import frame_from_grid, prepare_weekly, tail_weeks


@dataclass
class AdaptationDecision:
    use_revision_adjustment: bool = False
    compare_windows: bool = False
    downweight_models: list[str] = field(default_factory=list)
    interval_scale: float = 1.0
    notes: list[str] = field(default_factory=list)


def decide(alerts: list[AlertDraft], config: dict | None = None) -> AdaptationDecision:
    config = config or {}
    categories = {alert.category for alert in alerts}
    decision = AdaptationDecision()
    if "suspected_reporting_change" in categories:
        decision.use_revision_adjustment = True
        decision.notes.append("疑似报告过程变化：优先调整修订，保留原来的预测候选。")
    if "suspected_epidemic_change" in categories:
        decision.compare_windows = True
        if config.get("widen_intervals_on_epidemic", True):
            decision.interval_scale = float(config.get("epidemic_interval_scale", 1.25))
            decision.notes.append("疑似疫情动态变化：比较短训练窗口和长训练窗口。区间宽度倍率要由回放评价，不保证名义覆盖率。")
        else:
            decision.notes.append("疑似疫情动态变化：比较短训练窗口和长训练窗口。")
    if "insufficient_evidence" in categories:
        decision.notes.append("证据不足：保留多个候选，并提示不确定性。")
    if "data_quality" in categories:
        decision.notes.append("数据质量问题：缺失值不作为 0 进入训练。")
    for alert in alerts:
        if alert.rule == "forecast_error_deterioration":
            decision.downweight_models.extend(alert.affected_models)
            decision.notes.append("已成熟预测的误差持续恶化：降低对应模型权重。")
    return decision


def choose_window(short_wis: float | None, long_wis: float | None, short_ok: bool, long_ok: bool) -> tuple[str, str]:
    if not short_ok or short_wis is None:
        return "long", "短窗口未通过验证，继续使用较长训练窗口或原模型。"
    if not long_ok or long_wis is None:
        return "long", "长窗口未能完成验证评分，继续使用原模型或基线。"
    if short_wis < long_wis * 0.9:
        return "short", "短窗口在截止时间之前的验证周上 WIS 更低，采用较短训练窗口。"
    return "long", "短窗口没有优于长窗口，继续使用较长训练窗口。"


def validation_wis(frame: pd.DataFrame, window: int | None, validation_weeks: int = 6) -> tuple[float | None, bool]:
    """在 cutoff 之内留出最近若干周，比较不同训练窗口。不读取 cutoff 之后的数据。"""
    index, values = prepare_weekly(frame)
    if len(values) <= validation_weeks + 8:
        return None, False
    history_index = index[:-validation_weeks]
    history_values = values[:-validation_weeks]
    if window is not None:
        history_index = history_index[-window:]
        history_values = history_values[-window:]
    model = DampedTrendModel()
    fit = model.fit(
        frame_from_grid(history_index, history_values),
        {"min_obs": 8, "min_residuals": 4, "horizons": list(range(1, validation_weeks + 1))},
    )
    if fit.status != "success":
        return None, False
    predicted = model.predict(list(range(1, validation_weeks + 1)))
    if predicted.status != "success":
        return None, False
    scores = []
    for horizon in range(1, validation_weeks + 1):
        actual = values[-validation_weeks + horizon - 1]
        if not np.isfinite(actual):
            continue
        quantiles = predicted.horizons[horizon].quantiles
        scored = score_forecast(
            float(actual),
            quantiles[0.5],
            quantiles[0.025],
            quantiles[0.1],
            quantiles[0.25],
            quantiles[0.75],
            quantiles[0.9],
            quantiles[0.975],
        )
        scores.append(scored.wis)
    if not scores:
        return None, False
    return float(np.mean(scores)), True


def candidate_note(results: list[PredictResult]) -> str | None:
    failed = [result.model_id for result in results if result.status != "success"]
    if not failed:
        return None
    if any(result.model_id == "persistence" and result.status == "success" for result in results):
        return "部分候选未通过验证，组合继续使用成功的原模型或基线：" + "、".join(failed) + "。"
    return "候选模型未通过验证，且基线也不可用。"
