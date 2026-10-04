from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExperimentCreate(BaseModel):
    name: str | None = None
    disease_id: str
    region_ids: list[str] = Field(min_length=1)
    target_metric: str = "hospital_admissions"
    cutoff_time: str
    train_window_weeks: int | None = 52
    horizons: list[int] = Field(default_factory=lambda: [1, 2, 3, 4])
    models: list[str] | None = None
    replay_mode: str = "vintage"
    maturity_lag_days: int = 28
    random_seed: int = 42
    short_window_weeks: int = 26
    origins: list[str] | None = None
    ablations: dict[str, bool] | None = None
    config: dict[str, Any] | None = None


class AlertReviewUpdate(BaseModel):
    review_status: str
