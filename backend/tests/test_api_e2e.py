from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import aerp.db.session as db_session
from aerp.api.main import app
from aerp.config import get_settings
from aerp.db.session import Base, get_db
from aerp.services.seed import seed_synthetic


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    get_settings.cache_clear()
    monkeypatch.setenv("AERP_SYNC_TASKS", "true")
    monkeypatch.setenv("AERP_AUTO_SEED", "false")
    monkeypatch.setenv("AERP_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    get_settings.cache_clear()

    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(db_session, "engine", engine)
    monkeypatch.setattr(db_session, "SessionLocal", TestingSession)

    def _get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    db = TestingSession()
    seed_synthetic(db, seed=42, force=True)
    db.close()

    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_analog_library_and_match(client: TestClient):
    catalog = client.get("/api/analogs")
    assert catalog.status_code == 200
    body = catalog.json()
    assert body["items"]
    assert all(item["trainable_series"] is False for item in body["items"])
    regions = client.get("/api/regions")
    assert regions.status_code == 200
    first = regions.json()[0]
    assert first["context"]["available"]
    matched = client.post(
        "/api/analogs/match",
        json={"disease_id": "synth-ari", "region_id": "R1"},
    )
    assert matched.status_code == 200, matched.text
    payload = matched.json()
    assert payload["status"] in {"success", "degraded"}
    assert "disclaimer" in payload
    overview = client.get("/api/overview", params={"disease_id": "synth-ari", "region_id": "R1"})
    assert overview.status_code == 200
    assert "analog" in overview.json()


def test_health_and_template(client: TestClient):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert "合成" in health.json()["banner"]
    template = client.get("/api/data/template")
    assert template.status_code == 200
    assert "disease_id" in template.text


def test_csv_preview_rejects_bad_rows(client: TestClient):
    bad = "disease_id,region_id,period_start,period_end,issued_at,metric,value,unit,source_id,quality_flag\n"
    bad += "x,y,2024-01-01,2024-01-07,2024-01-08,hospital_admissions,abc,count,manual,provisional\n"
    response = client.post("/api/data/preview", files={"file": ("bad.csv", bad.encode("utf-8"), "text/csv")})
    assert response.status_code == 200
    body = response.json()
    assert body["errors"]


def test_end_to_end_experiment(client: TestClient):
    overview = client.get("/api/overview", params={"disease_id": "synth-ari", "region_id": "R1"})
    assert overview.status_code == 200
    cutoff = overview.json()["quality"]["cutoff_time"]
    created = client.post(
        "/api/experiments",
        json={
            "name": "测试回放",
            "disease_id": "synth-ari",
            "region_ids": ["R1", "R2"],
            "cutoff_time": cutoff,
            "replay_mode": "vintage",
            "train_window_weeks": 40,
            "models": ["persistence", "equal_weight", "dynamic_weight", "adaptive"],
            "ablations": {"no_revision": False, "no_anomaly": False, "no_dynamic_weight": True},
            "origins": ["2024-08-05", "2024-10-07", "2024-12-02", cutoff],
        },
    )
    assert created.status_code == 200, created.text
    experiment_id = created.json()["id"]
    ran = client.post(f"/api/experiments/{experiment_id}/run")
    assert ran.status_code == 200, ran.text
    detail = client.get(f"/api/experiments/{experiment_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "completed", body.get("error_message")
    assert body["summary"].get("n_scored", 0) > 0 or body["summary"].get("models")
    forecasts_response = client.get("/api/forecasts", params={"experiment_id": experiment_id})
    assert forecasts_response.status_code == 200, forecasts_response.text
    forecasts = forecasts_response.json()
    assert isinstance(forecasts, list)
    assert len(forecasts) > 0, body.get("summary")
    for row in forecasts[:20]:
        assert row["median"] >= 0
        q025, q975 = row["interval_95"]
        q10, q90 = row["interval_80"]
        q25, q75 = row["interval_50"]
        assert q025 <= q10 <= q25 <= row["median"] <= q75 <= q90 <= q975
    export = client.get(f"/api/experiments/{experiment_id}/export")
    assert export.status_code == 200
    assert "config_hash" in export.text


def test_ablation_suite_compares_without_forced_winner(client: TestClient):
    overview = client.get("/api/overview", params={"disease_id": "synth-ari", "region_id": "R1"})
    cutoff = overview.json()["quality"]["cutoff_time"]
    response = client.post(
        "/api/experiments/ablation-suite",
        json={
            "name": "消融单元测试",
            "disease_id": "synth-ari",
            "region_ids": ["R1"],
            "cutoff_time": cutoff,
            "replay_mode": "vintage",
            "train_window_weeks": 40,
            "models": ["persistence", "equal_weight", "adaptive"],
            "origins": ["2024-10-07", cutoff],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # 测试环境 AERP_SYNC_TASKS=true，会直接跑完并返回 suite 结果
    result = body.get("result") or body
    assert body.get("status") in {"completed", "queued", "running"}
    if body.get("status") == "completed":
        assert len(result["variants"]) == 7
        assert "comparison" in result
        assert "不默认优于" in result["comparison"]["note"] or "真实回放" in result["comparison"]["note"]
        assert all(item["status"] in {"completed", "failed"} for item in result["variants"])


def test_disease_registry_and_measles_isolation(client: TestClient):
    diseases = client.get("/api/diseases")
    assert diseases.status_code == 200
    rows = diseases.json()
    by_id = {item["id"]: item for item in rows}
    assert "influenza" in by_id
    assert "covid19" in by_id
    assert "measles" in by_id
    assert by_id["measles"]["status"] == "demo_only"
    assert by_id["dengue"]["status"] == "pending"
    seeded = client.post("/api/demo/seed-measles?force=true")
    assert seeded.status_code == 200
    measles_regions = client.get("/api/regions", params={"disease_id": "measles"})
    flu_regions = client.get("/api/regions", params={"disease_id": "synth-ari"})
    measles_ids = {item["id"] for item in measles_regions.json()}
    flu_ids = {item["id"] for item in flu_regions.json()}
    assert measles_ids and measles_ids.isdisjoint(flu_ids)
    overview = client.get("/api/overview", params={"disease_id": "measles", "region_id": "M1"})
    assert overview.status_code == 200
    assert overview.json()["metric"] == "weekly_cases"
    transfer = client.get("/api/transfer/demo")
    assert transfer.status_code == 200
    body = transfer.json()
    assert body["data_mode"] == "synthetic_stress_test"
    assert body["models"]["transfer_local"]["n"] > 0
