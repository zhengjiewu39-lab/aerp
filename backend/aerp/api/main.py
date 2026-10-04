from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aerp import __version__
from aerp.api.routes import api_router
from aerp.config import get_settings
from aerp.core.disclaimer import DISCLAIMER
from aerp.services.seed import ensure_seeded
from aerp.services.tasks import recover_interrupted_runs


@asynccontextmanager
async def lifespan(application: FastAPI):
    from aerp.db import session as db_session

    db_session.init_db()
    interrupted = recover_interrupted_runs()
    db = db_session.SessionLocal()
    try:
        ensure_seeded(db)
    finally:
        db.close()
    application.state.interrupted_on_boot = interrupted
    yield


settings = get_settings()
app = FastAPI(
    title="Adaptive Epidemic Research Platform",
    version=__version__,
    description=DISCLAIMER,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api")


@app.get("/")
def root():
    return {
        "name": "AERP",
        "version": __version__,
        "docs": "/docs",
        "api": "/api/health",
        "disclaimer": DISCLAIMER,
    }
