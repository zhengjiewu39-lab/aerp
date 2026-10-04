from __future__ import annotations

import numpy as np

# 50%、80%、95% 中心区间对应的尾部概率，以及中位数。
QUANTILE_LEVELS: tuple[float, ...] = (0.025, 0.1, 0.25, 0.5, 0.75, 0.9, 0.975)


def ordered_nonnegative(values: dict[float, float]) -> dict[float, float]:
    """强制预测计数非负，且分位数随水平非降。"""
    levels = sorted(float(level) for level in values)
    sequence = [max(0.0, float(values[level])) for level in levels]
    for index in range(1, len(sequence)):
        if sequence[index] < sequence[index - 1]:
            sequence[index] = sequence[index - 1]
    return {level: sequence[index] for index, level in enumerate(levels)}


def empirical_predictive_quantiles(
    point: float,
    errors: np.ndarray,
    levels: tuple[float, ...] = QUANTILE_LEVELS,
    min_residuals: int = 8,
) -> dict[float, float] | None:
    """用历史上的预测误差分位数平移当前点预测。误差必须只来自过去。"""
    sample = np.asarray(errors, dtype=float)
    sample = sample[np.isfinite(sample)]
    if sample.size < min_residuals:
        return None
    raw = {level: float(point) + float(np.quantile(sample, level)) for level in levels}
    return ordered_nonnegative(raw)


def scale_around_median(quantiles: dict[float, float], scale: float) -> dict[float, float]:
    """适应规则使用的区间宽度倍率。调用方必须在回放里报告实际覆盖率。"""
    median = float(quantiles[0.5])
    scaled = {level: median + (float(value) - median) * float(scale) for level, value in quantiles.items()}
    scaled[0.5] = median
    return ordered_nonnegative(scaled)
