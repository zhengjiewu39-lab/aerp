from __future__ import annotations

import numpy as np
import pandas as pd


def paired_block_intervals(
    left: np.ndarray,
    right: np.ndarray,
    *,
    block_ids: np.ndarray | None = None,
    n_boot: int = 500,
    seed: int = 42,
    alpha: float = 0.05,
) -> dict:
    """配对分块重采样差异均值区间。不把重叠预测当成独立样本。

    block_ids 通常是（地区, 原点）或季节。同一块内的多时长预测一起抽取。
    """
    left = np.asarray(left, dtype=float)
    right = np.asarray(right, dtype=float)
    if left.size != right.size or left.size == 0:
        return {"mean_diff": float("nan"), "low": float("nan"), "high": float("nan"), "n": int(left.size), "note": "样本不足"}
    diff = left - right
    if block_ids is None:
        block_ids = np.arange(len(diff))
    blocks = pd.Series(diff).groupby(block_ids)
    keys = list(blocks.groups)
    rng = np.random.default_rng(int(seed))
    means = []
    for _ in range(int(n_boot)):
        draw = rng.choice(keys, size=len(keys), replace=True)
        sample = np.concatenate([blocks.get_group(key).to_numpy() for key in draw])
        means.append(float(np.mean(sample)))
    means = np.asarray(means, dtype=float)
    return {
        "mean_diff": float(np.mean(diff)),
        "low": float(np.quantile(means, alpha / 2)),
        "high": float(np.quantile(means, 1 - alpha / 2)),
        "n": int(len(diff)),
        "n_blocks": int(len(keys)),
        "n_boot": int(n_boot),
        "seed": int(seed),
        "note": "按块重采样，考虑时间/地区相关和重叠预测。不是把每条预测当独立样本。",
    }
