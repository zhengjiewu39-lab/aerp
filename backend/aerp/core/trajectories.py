from __future__ import annotations

import numpy as np
from scipy.stats import norm

from aerp.core.quantiles import QUANTILE_LEVELS


def invert_quantile_function(quantiles: dict[float, float], uniforms: np.ndarray) -> np.ndarray:
    """把 (0,1) 样本经分段线性分位函数映到计数。超出已给分位水平时夹到两端，不外推冒充尾部。"""
    if not quantiles:
        return np.full(np.asarray(uniforms).shape, np.nan)
    levels = np.array(sorted(float(level) for level in quantiles), dtype=float)
    values = np.array([float(quantiles[float(level)]) for level in levels], dtype=float)
    u = np.clip(np.asarray(uniforms, dtype=float), float(levels[0]), float(levels[-1]))
    return np.interp(u, levels, values)


def gaussian_copula_uniforms(n: int, n_horizons: int, rho: float, rng: np.random.Generator) -> np.ndarray:
    """AR(1) 高斯 copula 得到相关均匀边际。rho=0 即独立。"""
    n_h = max(1, int(n_horizons))
    rho = float(np.clip(rho, -0.99, 0.99))
    z = np.zeros((int(n), n_h), dtype=float)
    z[:, 0] = rng.normal(size=int(n))
    noise_scale = float(np.sqrt(max(0.0, 1.0 - rho * rho)))
    for horizon in range(1, n_h):
        z[:, horizon] = rho * z[:, horizon - 1] + noise_scale * rng.normal(size=int(n))
    return np.asarray(norm.cdf(z), dtype=float)


def sample_copula_trajectories(
    quantile_by_horizon: dict[int, dict[float, float]],
    *,
    n_samples: int = 200,
    rho: float = 0.65,
    seed: int = 0,
) -> dict:
    """由各时长分位数生成连贯样本轨迹。这不是真实联合后验，但是比独立边际更一致。"""
    horizons = [int(h) for h in sorted(quantile_by_horizon)]
    if not horizons:
        return {"status": "error", "message": "没有分位数，无法抽样轨迹。", "paths": np.zeros((0, 0)), "horizons": []}
    rng = np.random.default_rng(int(seed))
    uniforms = gaussian_copula_uniforms(int(n_samples), len(horizons), float(rho), rng)
    columns = [invert_quantile_function(quantile_by_horizon[h], uniforms[:, index]) for index, h in enumerate(horizons)]
    paths = np.column_stack(columns) if columns else np.zeros((int(n_samples), 0))
    return {
        "status": "success",
        "method": "gaussian_copula_ar1",
        "rho": float(np.clip(rho, -0.99, 0.99)),
        "n_samples": int(n_samples),
        "horizons": horizons,
        "paths": paths,
        "note": "高斯 copula 只诱导相关结构，不是机制模型的联合分布。",
    }


def paths_from_sample_table(frame, *, horizon_col="horizon", sample_col="sample_id", value_col="value") -> dict:
    """FluSight sample 格式或内部修订样本：同一 sample_id 跨时长为一条轨迹。"""
    if frame is None or getattr(frame, "empty", True):
        return {"status": "error", "message": "没有样本表。", "paths": np.zeros((0, 0)), "horizons": []}
    work = frame.loc[:, [horizon_col, sample_col, value_col]].copy()
    work[horizon_col] = work[horizon_col].astype(int)
    horizons = sorted(int(h) for h in work[horizon_col].unique())
    wide = work.pivot_table(index=sample_col, columns=horizon_col, values=value_col, aggfunc="mean")
    wide = wide.reindex(columns=horizons)
    paths = wide.to_numpy(dtype=float)
    finite = np.isfinite(paths).all(axis=1)
    paths = paths[finite]
    if paths.size == 0:
        return {"status": "error", "message": "样本轨迹在所需时长上不完整。", "paths": np.zeros((0, 0)), "horizons": horizons}
    return {
        "status": "success",
        "method": "sample_table",
        "n_samples": int(paths.shape[0]),
        "horizons": horizons,
        "paths": paths,
        "note": "使用提交或修订给出的样本轨迹，不额外假设独立。",
    }


def estimate_error_ar1(errors_h1: np.ndarray, errors_h2: np.ndarray) -> float:
    """用同一原点 h=1 与 h=2 的已实现误差估计 AR(1) 相关。样本不足时退回默认。"""
    left = np.asarray(errors_h1, dtype=float)
    right = np.asarray(errors_h2, dtype=float)
    mask = np.isfinite(left) & np.isfinite(right)
    if int(mask.sum()) < 8:
        return 0.65
    if float(np.std(left[mask])) < 1e-8 or float(np.std(right[mask])) < 1e-8:
        return 0.65
    rho = float(np.corrcoef(left[mask], right[mask])[0, 1])
    if not np.isfinite(rho):
        return 0.65
    return float(np.clip(rho, -0.99, 0.99))


def default_quantile_grid() -> tuple[float, ...]:
    return QUANTILE_LEVELS
