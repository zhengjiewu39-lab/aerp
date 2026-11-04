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
from aerp.core.geo import country_collection, iter_features, state_collection
from aerp.db.session import Base, get_db
from aerp.services.geo_catalog import ensure_geo_catalog, reset_geo_catalog_cache
from aerp.services.seed import seed_map_demo
from aerp.services.who_covid import import_fixture


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    get_settings.cache_clear()
    reset_geo_catalog_cache()
    monkeypatch.setenv("AERP_SYNC_TASKS", "true")
    monkeypatch.setenv("AERP_AUTO_SEED", "false")
    monkeypatch.setenv("AERP_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("AERP_AI_PROVIDER", "mock")
    monkeypatch.setenv("AERP_AI_ALLOW_MOCK", "true")
    monkeypatch.setenv("AERP_AI_MODEL", "mock-test")
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
    ensure_geo_catalog(db)
    import_fixture(db)
    db.close()

    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()
    reset_geo_catalog_cache()


def test_boundary_feature_count_matches_catalog(client: TestClient):
    units = client.get("/api/geo/units", params={"level": "country"})
    assert units.status_code == 200
    body = units.json()
    n_poly = len(country_collection()["features"])
    n_points = len([item for item in iter_features("country") if item["properties"].get("representative_point")])
    assert body["n"] == n_poly + n_points
    bounds = client.get("/api/geo/boundaries", params={"level": "country"})
    geo = bounds.json()
    assert geo["n_features"] == n_poly
    kinds = {item["geometry"]["type"] for item in geo["geojson"]["features"]}
    assert "MultiPolygon" in kinds
    russia = next(item for item in geo["geojson"]["features"] if item["properties"]["iso2"] == "RU")
    fiji = next(item for item in geo["geojson"]["features"] if item["properties"]["iso2"] == "FJ")
    assert russia["geometry"]["type"] in {"Polygon", "MultiPolygon"}
    assert fiji["geometry"]["type"] in {"Polygon", "MultiPolygon"}
    sg = next(item for item in body["items"] if item["geo_id"] == "iso3166-1:SG")
    assert sg["representative_point"] is True


def test_us_states_not_copied_to_other_countries(client: TestClient):
    states = client.get("/api/geo/boundaries", params={"level": "adm1", "parent_geo_id": "iso3166-1:US"})
    payload = states.json()
    assert payload["available"]
    assert payload["n_features"] == len(state_collection()["features"])
    empty = client.get("/api/geo/boundaries", params={"level": "adm1", "parent_geo_id": "iso3166-1:BR"})
    assert empty.json()["available"] is False
    world = client.get(
        "/api/map/observations",
        params={"disease_id": "covid19", "metric": "weekly_cases", "view": "count", "time_mode": "unified"},
    )
    body = world.json()
    geos = {item["geo_id"] for item in body["features"] if item.get("data_status") not in {"no_data"}}
    assert "iso3166-1:US" in geos
    assert "iso3166-1:BR" in geos
    assert not any(geo.startswith("iso3166-2:") for geo in geos)


def test_zero_is_not_missing_and_unmatched_reported(client: TestClient, tmp_path: Path):
    world = client.get(
        "/api/map/observations",
        params={"disease_id": "covid19", "metric": "weekly_cases", "view": "count", "period_end": "2024-03-02"},
    )
    body = world.json()
    by_id = {item["geo_id"]: item for item in body["features"]}
    assert by_id["iso3166-1:BR"]["data_status"] == "zero"
    assert by_id["iso3166-1:BR"]["count"] == 0
    assert by_id["iso3166-1:CN"]["data_status"] == "no_data"
    assert by_id["iso3166-1:CN"]["value"] is None
    assert by_id["iso3166-1:CN"]["color"] != by_id["iso3166-1:BR"]["color"]
    coverage = client.get("/api/map/coverage", params={"disease_id": "covid19", "metric": "weekly_cases"})
    assert coverage.json()["n_without_data"] > 0


def test_unified_vs_latest_and_fixed_breaks(client: TestClient):
    unified = client.get(
        "/api/map/observations",
        params={"disease_id": "covid19", "metric": "weekly_cases", "view": "count", "time_mode": "unified", "period_end": "2024-03-02"},
    ).json()
    latest = client.get(
        "/api/map/observations",
        params={"disease_id": "covid19", "metric": "weekly_cases", "view": "count", "time_mode": "latest", "period_end": "2024-03-09"},
    ).json()
    assert unified["time_mode"] == "unified"
    assert latest["time_mode"] == "latest"
    us_latest = next(item for item in latest["features"] if item["geo_id"] == "iso3166-1:US")
    assert us_latest.get("lag_days") is not None
    assert unified["classification"]["fixed"] is True
    colors = unified["classification"]["colors"]
    assert colors[0].lower().startswith("#fff") or colors[0].lower().startswith("#ffc")
    assert colors[-1].lower() in {"#b71c1c", "#7f0000", "#e53935"}
    later = client.get(
        "/api/map/observations",
        params={"disease_id": "covid19", "metric": "weekly_cases", "view": "count", "time_mode": "unified", "period_end": "2024-03-09"},
    ).json()
    # default builder still returns fixed=True so playback does not silently switch meaning
    assert later["classification"]["fixed"] is True


def test_map_detail_matches_export_snapshot(client: TestClient):
    world = client.get(
        "/api/map/observations",
        params={"disease_id": "covid19", "metric": "weekly_cases", "view": "rate_per_100k", "period_end": "2024-03-02"},
    ).json()
    us = next(item for item in world["features"] if item["geo_id"] == "iso3166-1:US")
    detail = client.get(
        "/api/map/regions/iso3166-1:US",
        params={"disease_id": "covid19", "metric": "weekly_cases", "period_end": "2024-03-02"},
    ).json()
    assert (detail.get("current") or {}).get("count") == us.get("count")
    assert detail["snapshot_hash"] == world["snapshot_hash"]


def test_ai_unconfigured_does_not_fake(monkeypatch, client: TestClient):
    monkeypatch.setenv("AERP_AI_PROVIDER", "")
    monkeypatch.setenv("AERP_AI_ALLOW_MOCK", "false")
    monkeypatch.setenv("AERP_AI_API_KEY", "")
    get_settings.cache_clear()
    status = client.get("/api/ai/status").json()
    assert status["configured"] is False
    created = client.post(
        "/api/analyses/ai",
        json={"disease_id": "covid19", "geo_id": "iso3166-1:US", "metric": "weekly_cases", "period_end": "2024-03-02"},
    )
    assert created.status_code == 503
    get_settings.cache_clear()
    monkeypatch.setenv("AERP_AI_PROVIDER", "mock")
    monkeypatch.setenv("AERP_AI_ALLOW_MOCK", "true")
    get_settings.cache_clear()


def test_ai_review_and_stale_snapshot(client: TestClient):
    created = client.post(
        "/api/analyses/ai",
        json={"disease_id": "covid19", "geo_id": "iso3166-1:US", "metric": "weekly_cases", "view": "count", "period_end": "2024-03-02"},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["mode"] == "ai"
    assert body["provider"] == "mock"
    assert body["draft_banner"] == "AI草稿，待人工确认"
    assert body["official_ok"] is False
    exported = client.get(f"/api/analyses/{body['id']}/export").json()
    assert exported["official"] is False
    assert "未审核" in exported["review_required_banner"]

    bad = client.post(
        f"/api/analyses/{body['id']}/review",
        json={
            "action": "confirm",
            "expected_revision_id": body["current_revision_id"],
            "snapshot_hash": "not-the-hash",
        },
    )
    assert bad.status_code == 409

    if body["status"] == "needs_correction":
        edited = {
            "summary": "观测见证据。",
            "observations": [{"text": "见 e1", "evidence_ids": ["e1"]}],
            "possible_explanations": [{"text": "未知", "evidence_ids": ["e1"]}],
            "uncertainties": [{"text": "测试桩", "evidence_ids": ["e1"]}],
            "data_quality_issues": [{"text": "夹具", "evidence_ids": ["e1"]}],
            "suggested_checks": [{"text": "核对来源", "evidence_ids": ["e1"]}],
            "citations": [{"evidence_id": "e1", "note": "当前观测"}],
        }
        confirm = client.post(
            f"/api/analyses/{body['id']}/review",
            json={
                "action": "edit_confirm",
                "expected_revision_id": body["current_revision_id"],
                "snapshot_hash": body["snapshot_hash"],
                "edited": edited,
                "comment": "修正引用后确认",
            },
        )
    else:
        confirm = client.post(
            f"/api/analyses/{body['id']}/review",
            json={
                "action": "confirm",
                "expected_revision_id": body["current_revision_id"],
                "snapshot_hash": body["snapshot_hash"],
                "comment": "人工确认",
            },
        )
    assert confirm.status_code == 200, confirm.text
    confirmed = confirm.json()
    assert confirmed["status"] == "confirmed"
    assert confirmed["source_note"] == "AI辅助、人工审核"
    revisions = client.get(f"/api/analyses/{body['id']}/revisions").json()
    assert len(revisions) >= 1

    again = client.post(
        f"/api/analyses/{body['id']}/review",
        json={
            "action": "confirm",
            "expected_revision_id": confirmed["current_revision_id"],
            "snapshot_hash": confirmed["snapshot_hash"],
        },
    )
    assert again.status_code == 409


def test_manual_analysis_not_labelled_ai(client: TestClient):
    created = client.post(
        "/api/analyses/manual",
        json={
            "disease_id": "covid19",
            "geo_id": "iso3166-1:BR",
            "metric": "weekly_cases",
            "period_end": "2024-03-02",
            "view": "count",
            "as_draft": True,
            "structured": {
                "summary": "巴西该周报告为零。",
                "observations": [{"text": "报告为零", "evidence_ids": ["e2"]}],
                "possible_explanations": [{"text": "低传播或低检测都可能", "evidence_ids": ["e2"]}],
                "uncertainties": [{"text": "夹具数据", "evidence_ids": ["e1"]}],
                "data_quality_issues": [{"text": "测试夹具", "evidence_ids": ["e6"]}],
                "suggested_checks": [{"text": "核对 WHO 原始日值", "evidence_ids": ["e7"]}],
                "citations": [{"evidence_id": "e2", "note": "零值"}],
            },
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["mode"] == "manual"
    assert body["provider"] == "manual"
    assert body["source_note"] == "手动分析"
    reject = client.post(
        f"/api/analyses/{body['id']}/review",
        json={
            "action": "reject",
            "expected_revision_id": body["current_revision_id"],
            "snapshot_hash": body["snapshot_hash"],
            "comment": "仅测试驳回",
        },
    )
    assert reject.status_code == 200
    assert reject.json()["status"] == "rejected"


def test_map_demo_playback_does_not_replace_who_covid(client: TestClient):
    db = db_session.SessionLocal()
    seed_map_demo(db)
    db.close()
    covid = client.get(
        "/api/map/observations",
        params={"disease_id": "covid19", "metric": "weekly_cases", "view": "count", "period_end": "2024-03-02"},
    ).json()
    assert covid.get("watermark") in {None, ""}
    assert "who_covid19" in (covid.get("legend") or {}).get("sources", [])
    flu = client.get(
        "/api/map/observations",
        params={"disease_id": "influenza", "metric": "weekly_cases", "view": "rate_per_100k"},
    ).json()
    assert flu.get("is_demo") is True
    assert flu.get("watermark")
    assert len(flu.get("periods") or []) >= 8
    first_end = flu["periods"][max(0, len(flu["periods"]) // 4)]["period_end"]
    last_end = flu["periods"][-1]["period_end"]
    early = client.get(
        "/api/map/observations",
        params={"disease_id": "influenza", "metric": "weekly_cases", "view": "count", "period_end": first_end, "lock_period_end": last_end},
    ).json()
    late = client.get(
        "/api/map/observations",
        params={"disease_id": "influenza", "metric": "weekly_cases", "view": "count", "period_end": last_end, "lock_period_end": last_end},
    ).json()
    assert early["classification"]["breaks"] == late["classification"]["breaks"]

    def counts(payload: dict) -> dict[str, float]:
        return {
            item["geo_id"]: float(item["count"])
            for item in payload["features"]
            if item.get("count") is not None and item.get("data_status") not in {"no_data", "unreported"}
        }

    early_counts = counts(early)
    late_counts = counts(late)
    shared = set(early_counts) | set(late_counts)
    assert early_counts or late_counts
    assert any(early_counts.get(geo, 0) != late_counts.get(geo, 0) for geo in shared)
    dengue = client.get(
        "/api/map/observations",
        params={"disease_id": "dengue", "metric": "weekly_cases", "view": "count"},
    ).json()
    assert dengue.get("is_demo") is True
    assert len(dengue.get("periods") or []) >= 8
    stale = client.get(
        "/api/map/observations",
        params={"disease_id": "influenza", "metric": "weekly_deaths", "view": "rate_per_100k", "period_end": "2026-08-29"},
    ).json()
    assert stale.get("is_demo") is True
    assert stale["metric"] == "weekly_cases"
    assert stale["period_clamped"] is True
    assert str(stale["period_end"]).startswith("2023")
    valued = [
        item
        for item in stale["features"]
        if item.get("value") is not None and item.get("data_status") not in {"no_data", "unreported"}
    ]
    assert len(valued) >= 8


def test_burden_labels_and_absolute_class_mode(client: TestClient):
    relative = client.get(
        "/api/map/observations",
        params={"disease_id": "covid19", "metric": "weekly_cases", "view": "rate_per_100k", "period_end": "2024-03-02", "class_mode": "quantile"},
    ).json()
    assert relative["classification"]["labels"] == ["轻微", "一般", "较重", "严重", "非常严重"]
    assert relative["classification"]["is_official_severity"] is False
    assert "官方" in relative["classification"]["note"] or "临床" in relative["classification"]["note"]
    hist = {item["label"]: item["n"] for item in relative["class_histogram"]}
    assert "非常严重" in hist
    assert sum(hist.values()) == len(relative["features"])
    us = next(item for item in relative["features"] if item["geo_id"] == "iso3166-1:US")
    assert us.get("trend_dir") in {"up", "down", "stable", "unknown"}
    absolute = client.get(
        "/api/map/observations",
        params={"disease_id": "covid19", "metric": "weekly_cases", "view": "rate_per_100k", "period_end": "2024-03-02", "class_mode": "absolute"},
    ).json()
    assert absolute["classification"]["method"] == "absolute_rate_research"
    assert absolute["classification"]["breaks"][:5] == [0.0, 1.0, 5.0, 15.0, 40.0]
    later = client.get(
        "/api/map/observations",
        params={"disease_id": "covid19", "metric": "weekly_cases", "view": "rate_per_100k", "period_end": "2024-03-09", "class_mode": "absolute"},
    ).json()
    assert later["classification"]["breaks"] == absolute["classification"]["breaks"]
