from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

OBSERVED_COVARIATE_KEYS = {
    "hospital_admissions",
    "reported_cases",
    "tests",
    "positivity",
    "wastewater_index",
    "value",
}


@dataclass
class FitResult:
    status: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HorizonForecast:
    horizon: int
    quantiles: dict[float, float]
    method: str
    quality_note: str


@dataclass
class PredictResult:
    model_id: str
    status: str
    message: str
    horizons: dict[int, HorizonForecast] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def covariate_note(available_covariates: dict | None) -> str:
    if not available_covariates:
        return "未使用未来辅助变量观测；季节项由日历确定。"
    ignored = [key for key in available_covariates if key in OBSERVED_COVARIATE_KEYS]
    if ignored:
        names = "、".join(ignored)
        return f"已忽略未来辅助变量观测（{names}）。季节项只由日历确定。"
    return "未使用未来辅助变量观测；季节项由日历确定。"


def check_units(training_data: pd.DataFrame) -> str | None:
    if training_data is None or "unit" not in training_data.columns:
        return None
    units = {str(unit) for unit in training_data["unit"].dropna().unique()}
    if len(units) > 1:
        return "同一序列出现多个单位，已停止拟合，未混合换算。"
    return None
