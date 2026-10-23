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


def mix_quantile_average(items: list[dict[float, float]]) -> dict[float, float]:
    """对应分位数平均。这是一种组合启发式，不是混合分布的分位数。"""
    if not items:
        return {}
    levels = sorted(items[0])
    mixed = {level: float(np.mean([item[level] for item in items])) for level in levels}
    return ordered_nonnegative(mixed)


def mix_predictive_cdfs(items: list[dict[float, float]], *, n_grid: int = 400) -> dict[float, float]:
    """等权混合各预测分布的 CDF 后再反演分位数。这才是样本/分布混合。"""
    if not items:
        return {}
    levels = sorted({float(level) for item in items for level in item})
    lows = [min(item[level] for level in item) for item in items]
    highs = [max(item[level] for level in item) for item in items]
    grid = np.linspace(min(lows), max(highs) + 1e-6, int(n_grid))

    def cdf_of(item: dict[float, float], x: float) -> float:
        xs = np.array([item[level] for level in sorted(item)], dtype=float)
        ps = np.array(sorted(item), dtype=float)
        if x <= xs[0]:
            return float(ps[0])
        if x >= xs[-1]:
            return float(ps[-1])
        return float(np.interp(x, xs, ps))

    mix_cdf = np.mean([[cdf_of(item, float(x)) for item in items] for x in grid], axis=1)
    mixed = {}
    for level in levels:
        mixed[level] = float(np.interp(level, mix_cdf, grid))
    return ordered_nonnegative(mixed)


def empirical_quantiles_from_samples(samples: np.ndarray, levels: tuple[float, ...] = QUANTILE_LEVELS) -> dict[float, float]:
    values = np.asarray(samples, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {}
    return ordered_nonnegative({float(level): float(np.quantile(values, level)) for level in levels})


def scale_around_median(quantiles: dict[float, float], scale: float) -> dict[float, float]:
    """适应规则使用的区间宽度倍率。调用方必须在回放里报告实际覆盖率。"""
    median = float(quantiles[0.5])
    scaled = {level: median + (float(value) - median) * float(scale) for level, value in quantiles.items()}
    scaled[0.5] = median
    return ordered_nonnegative(scaled)
