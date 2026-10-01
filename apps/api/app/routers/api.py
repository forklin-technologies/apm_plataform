from fastapi import APIRouter

from app.routers import health

# Web and API share one origin behind a proxy; the API lives under /api (no CORS).
api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
