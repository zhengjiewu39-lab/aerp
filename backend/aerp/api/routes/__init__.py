from fastapi import APIRouter

from aerp.api.routes import alerts, analogs, analyses, data, experiments, geo, map as map_routes, meta, observations

api_router = APIRouter()
api_router.include_router(meta.router, tags=["meta"])
api_router.include_router(data.router, tags=["data"])
api_router.include_router(observations.router, tags=["observations"])
api_router.include_router(experiments.router, tags=["experiments"])
api_router.include_router(alerts.router, tags=["alerts"])
api_router.include_router(analogs.router, tags=["analogs"])
api_router.include_router(geo.router, tags=["geo"])
api_router.include_router(map_routes.router, tags=["map"])
api_router.include_router(analyses.router, tags=["analyses"])
