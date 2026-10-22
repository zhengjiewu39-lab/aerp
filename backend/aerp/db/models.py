from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aerp.db.session import Base


class Disease(Base):
    __tablename__ = "diseases"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class Region(Base):
    __tablename__ = "regions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    population: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scenario: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    context_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    url: Mapped[str] = mapped_column(String(512), default="")
    license: Mapped[str] = mapped_column(Text, default="")
    update_note: Mapped[str] = mapped_column(Text, default="")
    connector_status: Mapped[str] = mapped_column(String(64), default="unconfigured")


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), ForeignKey("data_sources.id"))
    file_hash: Mapped[str] = mapped_column(String(128))
    filename: Mapped[str] = mapped_column(String(256), default="")
    imported_at: Mapped[datetime] = mapped_column(DateTime)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class ObservationVintage(Base):
    __tablename__ = "observation_vintages"
    __table_args__ = (
        UniqueConstraint(
            "disease_id",
            "region_id",
            "metric",
            "period_start",
            "issued_at",
            "source_id",
            name="uq_observation_vintage",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    disease_id: Mapped[str] = mapped_column(String(64), ForeignKey("diseases.id"), index=True)
    region_id: Mapped[str] = mapped_column(String(64), ForeignKey("regions.id"), index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime, index=True)
    period_end: Mapped[datetime] = mapped_column(DateTime, index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    metric: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(64), ForeignKey("data_sources.id"))
    quality_flag: Mapped[str] = mapped_column(String(32))
    import_batch_id: Mapped[str] = mapped_column(String(64), ForeignKey("import_batches.id"), index=True)


class SimulationTruth(Base):
    __tablename__ = "simulation_truth"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    region_id: Mapped[str] = mapped_column(String(64), index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime)
    period_end: Mapped[datetime] = mapped_column(DateTime, index=True)
    metric: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column(Float)


class SimulationLabel(Base):
    __tablename__ = "simulation_labels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    region_id: Mapped[str] = mapped_column(String(64), index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime)
    period_end: Mapped[datetime] = mapped_column(DateTime)
    category: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(Text, default="")


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    disease_id: Mapped[str] = mapped_column(String(64), ForeignKey("diseases.id"))
    region_ids: Mapped[list] = mapped_column(JSON)
    target_metric: Mapped[str] = mapped_column(String(64), default="hospital_admissions")
    cutoff_time: Mapped[datetime] = mapped_column(DateTime)
    train_window_weeks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    horizons: Mapped[list] = mapped_column(JSON)
    models: Mapped[list] = mapped_column(JSON)
    replay_mode: Mapped[str] = mapped_column(String(32), default="vintage")
    maturity_lag_days: Mapped[int] = mapped_column(Integer, default=28)
    random_seed: Mapped[int] = mapped_column(Integer, default=42)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    config_hash: Mapped[str] = mapped_column(String(128), default="")
    input_hash: Mapped[str] = mapped_column(String(128), default="")
    code_version: Mapped[str] = mapped_column(String(64), default="")
    dependency_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="created")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)

    runs: Mapped[list[ModelRun]] = relationship(back_populates="experiment")


class ModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(String(64), ForeignKey("experiments.id"), index=True)
    model_id: Mapped[str] = mapped_column(String(64), index=True)
    model_version: Mapped[str] = mapped_column(String(64), default="0.1.0")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    trained_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)

    experiment: Mapped[Experiment] = relationship(back_populates="runs")


class Forecast(Base):
    __tablename__ = "forecasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str] = mapped_column(String(64), ForeignKey("experiments.id"), index=True)
    model_run_id: Mapped[str] = mapped_column(String(64), ForeignKey("model_runs.id"), index=True)
    model_id: Mapped[str] = mapped_column(String(64), index=True)
    region_id: Mapped[str] = mapped_column(String(64), index=True)
    metric: Mapped[str] = mapped_column(String(64))
    issued_at: Mapped[datetime] = mapped_column(DateTime)
    cutoff_time: Mapped[datetime] = mapped_column(DateTime)
    target_period_start: Mapped[datetime] = mapped_column(DateTime)
    target_period_end: Mapped[datetime] = mapped_column(DateTime)
    horizon: Mapped[int] = mapped_column(Integer)
    q025: Mapped[float] = mapped_column(Float)
    q10: Mapped[float] = mapped_column(Float)
    q25: Mapped[float] = mapped_column(Float)
    q50: Mapped[float] = mapped_column(Float)
    q75: Mapped[float] = mapped_column(Float)
    q90: Mapped[float] = mapped_column(Float)
    q975: Mapped[float] = mapped_column(Float)
    interval_method: Mapped[str] = mapped_column(String(128), default="")
    quality_note: Mapped[str] = mapped_column(Text, default="")
    generated_at: Mapped[datetime] = mapped_column(DateTime)
    arm: Mapped[str] = mapped_column(String(64), default="main")
    weights: Mapped[dict] = mapped_column(JSON, default=dict)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("experiments.id"), nullable=True, index=True)
    region_id: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(64))
    level: Mapped[str] = mapped_column(String(32))
    rule: Mapped[str] = mapped_column(String(128))
    metrics: Mapped[list] = mapped_column(JSON, default=list)
    period_start: Mapped[datetime] = mapped_column(DateTime)
    period_end: Mapped[datetime] = mapped_column(DateTime)
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    import_batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    supporting_evidence: Mapped[str] = mapped_column(Text, default="")
    conflicting_evidence: Mapped[str] = mapped_column(Text, default="")
    limitations: Mapped[str] = mapped_column(Text, default="")
    affected_models: Mapped[list] = mapped_column(JSON, default=list)
    review_status: Mapped[str] = mapped_column(String(32), default="unreviewed")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    cutoff_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str] = mapped_column(String(64), ForeignKey("experiments.id"), index=True)
    forecast_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("forecasts.id"), nullable=True)
    model_id: Mapped[str] = mapped_column(String(64), index=True)
    region_id: Mapped[str] = mapped_column(String(64), index=True)
    horizon: Mapped[int] = mapped_column(Integer)
    target_period_end: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    actual_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    matured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    abs_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    wis: Mapped[float | None] = mapped_column(Float, nullable=True)
    covered_50: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    covered_80: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    covered_95: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    width_50: Mapped[float | None] = mapped_column(Float, nullable=True)
    width_80: Mapped[float | None] = mapped_column(Float, nullable=True)
    width_95: Mapped[float | None] = mapped_column(Float, nullable=True)
    phase: Mapped[str] = mapped_column(String(32), default="unknown")
    arm: Mapped[str] = mapped_column(String(64), default="main")
    result_version: Mapped[str] = mapped_column(String(64), default="mature_lag")
    notes: Mapped[str] = mapped_column(Text, default="")
