from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from aerp.db import models as _models  # noqa: F401
from aerp.db.models import (  # noqa: F401
    Alert,
    DataSource,
    Disease,
    Evaluation,
    Experiment,
    Forecast,
    ImportBatch,
    ModelRun,
    ObservationVintage,
    Region,
    SimulationLabel,
    SimulationTruth,
)
from aerp.db.session import Base


def research_session(db_path: Path) -> Session:
    """独立研究库，不写入演示用 aerp.db。"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_connection, _record) -> None:  # noqa: ARG001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session)()
