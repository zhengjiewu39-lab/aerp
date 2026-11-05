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
    purpose: Mapped[str] = mapped_column(String(32), default="final", index=True)
    split_set: Mapped[str] = mapped_column(String(32), default="unspecified", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class GeoUnit(Base):
    __tablename__ = "geo_units"

    geo_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    name_en: Mapped[str] = mapped_column(String(256), default="")
    level: Mapped[str] = mapped_column(String(32), index=True)
    parent_geo_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("geo_units.geo_id"), nullable=True, index=True)
    country_code: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    region_code: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    valid_from: Mapped[str | None] = mapped_column(String(16), nullable=True)
    valid_to: Mapped[str | None] = mapped_column(String(16), nullable=True)
    has_polygon: Mapped[bool] = mapped_column(Boolean, default=True)
    representative_point: Mapped[bool] = mapped_column(Boolean, default=False)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)


class GeoBoundary(Base):
    __tablename__ = "geo_boundaries"
    __table_args__ = (UniqueConstraint("geo_id", "boundary_version", name="uq_geo_boundary_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    geo_id: Mapped[str] = mapped_column(String(64), ForeignKey("geo_units.geo_id"), index=True)
    boundary_version: Mapped[str] = mapped_column(String(128), index=True)
    geometry_kind: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(128))
    license: Mapped[str] = mapped_column(Text, default="")
    file_ref: Mapped[str] = mapped_column(String(256), default="")
    checksum: Mapped[str] = mapped_column(String(128), default="")
    fetched_at: Mapped[str] = mapped_column(String(32), default="")


class RegionGeoMapping(Base):
    __tablename__ = "region_geo_mappings"
    __table_args__ = (UniqueConstraint("source_id", "source_region_id", "geo_id", name="uq_region_geo_map"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    source_region_id: Mapped[str] = mapped_column(String(64), index=True)
    geo_id: Mapped[str] = mapped_column(String(64), ForeignKey("geo_units.geo_id"), index=True)
    match_method: Mapped[str] = mapped_column(String(64))
    review_status: Mapped[str] = mapped_column(String(32), default="reviewed")
    note: Mapped[str] = mapped_column(Text, default="")


class PopulationEstimate(Base):
    __tablename__ = "population_estimates"
    __table_args__ = (UniqueConstraint("geo_id", "year", "source", name="uq_population_estimate"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    geo_id: Mapped[str] = mapped_column(String(64), ForeignKey("geo_units.geo_id"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    population: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(64), default="")


class MapClassification(Base):
    __tablename__ = "map_classifications"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    disease_id: Mapped[str] = mapped_column(String(64), index=True)
    metric: Mapped[str] = mapped_column(String(64), index=True)
    method: Mapped[str] = mapped_column(String(64))
    breaks: Mapped[list] = mapped_column(JSON, default=list)
    labels: Mapped[list] = mapped_column(JSON, default=list)
    colors: Mapped[list] = mapped_column(JSON, default=list)
    valid_from: Mapped[str | None] = mapped_column(String(16), nullable=True)
    valid_to: Mapped[str | None] = mapped_column(String(16), nullable=True)
    version: Mapped[str] = mapped_column(String(64), default="1")
    note: Mapped[str] = mapped_column(Text, default="")
    is_official_severity: Mapped[bool] = mapped_column(Boolean, default=False)


class EvidenceSnapshot(Base):
    __tablename__ = "evidence_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_hash: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class AnalysisRecord(Base):
    __tablename__ = "analysis_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("analysis_records.id"), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    disease_id: Mapped[str] = mapped_column(String(64), index=True)
    geo_id: Mapped[str] = mapped_column(String(64), index=True)
    metric: Mapped[str] = mapped_column(String(64))
    period_start: Mapped[str] = mapped_column(String(16))
    period_end: Mapped[str] = mapped_column(String(16))
    snapshot_id: Mapped[str] = mapped_column(String(64), ForeignKey("evidence_snapshots.id"), index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(128), index=True)
    provider: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    operator_name: Mapped[str] = mapped_column(String(128), default="local-researcher")
    identity_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    current_revision_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_revision_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)


class AnalysisRevision(Base):
    __tablename__ = "analysis_revisions"
    __table_args__ = (UniqueConstraint("analysis_id", "revision_no", name="uq_analysis_revision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_id: Mapped[str] = mapped_column(String(64), ForeignKey("analysis_records.id"), index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32))
    raw_text: Mapped[str] = mapped_column(Text, default="")
    structured: Mapped[dict] = mapped_column(JSON, default=dict)
    validation: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    created_by: Mapped[str] = mapped_column(String(128), default="")


class AiCallLog(Base):
    __tablename__ = "ai_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128), default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    live_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_id: Mapped[str] = mapped_column(String(64), ForeignKey("analysis_records.id"), index=True)
    revision_id: Mapped[int] = mapped_column(Integer, ForeignKey("analysis_revisions.id"))
    action: Mapped[str] = mapped_column(String(32))
    reviewer: Mapped[str] = mapped_column(String(128))
    comment: Mapped[str] = mapped_column(Text, default="")
    expected_snapshot_hash: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    identity_note: Mapped[str] = mapped_column(Text, default="")
