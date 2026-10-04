from fastapi import APIRouter

from aerp.api.routes import alerts, data, experiments, meta, observations

api_router = APIRouter()
api_router.include_router(meta.router, tags=["meta"])
api_router.include_router(data.router, tags=["data"])
api_router.include_router(observations.router, tags=["observations"])
api_router.include_router(experiments.router, tags=["experiments"])
api_router.include_router(alerts.router, tags=["alerts"])
