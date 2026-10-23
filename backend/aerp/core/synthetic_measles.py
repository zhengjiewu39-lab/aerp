"""明确标注的麻疹合成爆发示例。不是真实监测。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

DISEASE_ID = "measles"
SOURCE_ID = "synthetic-measles"
BATCH_ID = "synthetic-measles-seed"
METRIC = "weekly_cases"

REGIONS = (
    {"region_id": "M1", "name": "麻疹教学区甲", "population": 80_000, "note": "合成稀疏爆发"},
    {"region_id": "M2", "name": "麻疹教学区乙", "population": 50_000, "note": "合成零值为主"},
    {"region_id": "M3", "name": "麻疹教学区丙", "population": 120_000, "note": "合成双峰爆发"},
)


@dataclass
class MeaslesBundle:
    observations: pd.DataFrame
    n_weeks: int
    note: str


def generate_measles_demo(seed: int = 7, n_weeks: int = 52) -> MeaslesBundle:
    rng = np.random.default_rng(int(seed))
    ends = pd.date_range("2023-01-07", periods=n_weeks, freq="7D")
    rows = []
    for region in REGIONS:
        base = np.zeros(n_weeks, dtype=float)
        if region["region_id"] == "M1":
            peak = 18
            base[20:28] = rng.poisson(np.linspace(2, peak, 8))
            base[28:36] = rng.poisson(np.linspace(peak, 0, 8))
        elif region["region_id"] == "M2":
            base[rng.choice(n_weeks, size=3, replace=False)] = rng.integers(1, 4)
        else:
            base[8:14] = rng.poisson(np.linspace(1, 12, 6))
            base[14:20] = rng.poisson(np.linspace(12, 0, 6))
            base[34:42] = rng.poisson(np.linspace(1, 9, 8))
        for stamp, value in zip(ends, base):
            issued = stamp + pd.Timedelta(days=3)
            rows.append(
                {
                    "disease_id": DISEASE_ID,
                    "region_id": region["region_id"],
                    "period_start": stamp - pd.Timedelta(days=6),
                    "period_end": stamp,
                    "issued_at": issued,
                    "metric": METRIC,
                    "value": float(value),
                    "unit": "count",
                    "source_id": SOURCE_ID,
                    "quality_flag": "synthetic",
                }
            )
    frame = pd.DataFrame(rows)
    return MeaslesBundle(
        observations=frame,
        n_weeks=n_weeks,
        note="合成麻疹周病例，用于零值/稀疏样本压力测试。不是真实疫情，不可写入论文真实结果。",
    )
