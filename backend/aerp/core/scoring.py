from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# 名义覆盖率 -> Bracher 等 (2021) 的双侧尾部概率 alpha。
INTERVALS = (
    ("50", 0.5),
    ("80", 0.2),
    ("95", 0.05),
)


def interval_score(y: float, lower: float, upper: float, alpha: float) -> float:
    width = float(upper) - float(lower)
    if y < lower:
        return width + (2.0 / alpha) * (float(lower) - y)
    if y > upper:
        return width + (2.0 / alpha) * (y - float(upper))
    return width


def weighted_interval_score(y: float, median: float, bounds: dict[str, tuple[float, float]]) -> float:
    """加权区间评分，越低越好。

    WIS = 1/(K + 1/2) * (1/2 * |y - m| + sum_k (alpha_k / 2) * IS_{alpha_k})
    K = 3，对应 50%、80%、95% 区间。
    """
    total = 0.5 * abs(float(y) - float(median))
    for name, alpha in INTERVALS:
        lower, upper = bounds[name]
        total += (alpha / 2.0) * interval_score(float(y), float(lower), float(upper), alpha)
    return total / (len(INTERVALS) + 0.5)


@dataclass(frozen=True)
class ScoreResult:
    abs_error: float
    wis: float
    covered_50: bool
    covered_80: bool
    covered_95: bool
    width_50: float
    width_80: float
    width_95: float


def score_forecast(
    y: float,
    median: float,
    q025: float,
    q10: float,
    q25: float,
    q75: float,
    q90: float,
    q975: float,
) -> ScoreResult:
    bounds = {"50": (q25, q75), "80": (q10, q90), "95": (q025, q975)}
    return ScoreResult(
        abs_error=abs(float(y) - float(median)),
        wis=weighted_interval_score(float(y), float(median), bounds),
        covered_50=q25 <= y <= q75,
        covered_80=q10 <= y <= q90,
        covered_95=q025 <= y <= q975,
        width_50=float(q75) - float(q25),
        width_80=float(q90) - float(q10),
        width_95=float(q975) - float(q025),
    )


def mean_abs_error(y_true, y_pred) -> float:
    """MAE。待评价目标不应传入。"""
    truth = np.asarray(y_true, dtype=float)
    pred = np.asarray(y_pred, dtype=float)
    if truth.size == 0:
        return float("nan")
    return float(np.mean(np.abs(truth - pred)))
