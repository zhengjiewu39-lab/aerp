from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

from sqlalchemy import select

from aerp.config import get_settings
from aerp.db.models import Experiment


_executor = ThreadPoolExecutor(max_workers=1)
_lock = threading.Lock()
_futures: dict[str, object] = {}
_ablation_suites: dict[str, dict] = {}


def _session_factory():
    from aerp.db import session as db_session

    return db_session.SessionLocal


def recover_interrupted_runs() -> int:
    """应用启动时，把仍显示为 running / queued 的任务标记为 interrupted。"""
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        from aerp.db import session as db_session

        db_session.init_db()
        rows = db.scalars(select(Experiment).where(Experiment.status.in_(["running", "queued"]))).all()
        count = 0
        for row in rows:
            row.status = "interrupted"
            row.error_message = "应用重启后中断。本地原型不会自动恢复未完成任务。"
            count += 1
        db.commit()
        with _lock:
            for suite in _ablation_suites.values():
                if suite.get("status") in {"queued", "running"}:
                    suite["status"] = "interrupted"
                    suite["error_message"] = "应用重启后中断。"
        return count
    finally:
        db.close()


def _job(experiment_id: str) -> None:
    from aerp.services.experiment_engine import run_experiment

    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        run_experiment(db, experiment_id)
    except Exception:
        pass
    finally:
        db.close()
        with _lock:
            _futures.pop(experiment_id, None)


def submit_experiment(experiment_id: str) -> dict:
    settings = get_settings()
    if settings.aerp_sync_tasks:
        _job(experiment_id)
        return {
            "queued": False,
            "message": "同步执行完成（AERP_SYNC_TASKS=true）。本地原型调试模式。",
            "experiment_id": experiment_id,
        }
    with _lock:
        existing = _futures.get(experiment_id)
        if existing is not None and not existing.done():  # type: ignore[attr-defined]
            return {"queued": False, "message": "任务已在运行", "experiment_id": experiment_id}
        future = _executor.submit(_job, experiment_id)
        _futures[experiment_id] = future
    return {
        "queued": True,
        "message": "已提交后台任务（本地线程池，非生产级队列）",
        "experiment_id": experiment_id,
    }


def _ablation_job(suite_id: str, payload: dict) -> None:
    from aerp.services.ablation import run_ablation_suite

    with _lock:
        suite = _ablation_suites.get(suite_id)
        if suite is not None:
            suite["status"] = "running"
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        result = run_ablation_suite(db, payload)
        with _lock:
            suite = _ablation_suites.get(suite_id)
            if suite is not None:
                suite["status"] = "completed"
                suite["result"] = result
                suite["error_message"] = ""
    except Exception as exc:  # noqa: BLE001
        with _lock:
            suite = _ablation_suites.get(suite_id)
            if suite is not None:
                suite["status"] = "failed"
                suite["error_message"] = str(exc)
    finally:
        db.close()
        with _lock:
            _futures.pop(f"ablation:{suite_id}", None)


def submit_ablation_suite(payload: dict) -> dict:
    """后台运行消融套件，避免阻塞普通 API。"""
    settings = get_settings()
    suite_id = f"ablation-{uuid4().hex[:10]}"
    body = deepcopy(payload)
    with _lock:
        _ablation_suites[suite_id] = {
            "suite_id": suite_id,
            "status": "queued",
            "error_message": "",
            "result": None,
        }
    if settings.aerp_sync_tasks:
        _ablation_job(suite_id, body)
        return get_ablation_suite(suite_id)
    future = _executor.submit(_ablation_job, suite_id, body)
    with _lock:
        _futures[f"ablation:{suite_id}"] = future
    return {
        "suite_id": suite_id,
        "status": "queued",
        "queued": True,
        "message": "消融对照已提交后台线程，不会阻塞总览等普通接口。",
        "result": None,
    }


def get_ablation_suite(suite_id: str) -> dict | None:
    with _lock:
        suite = _ablation_suites.get(suite_id)
        if suite is None:
            return None
        return deepcopy(suite)
