from fastapi import APIRouter

from app.routers import health
from app.routers.v1 import auth, invitations

# Web and API share one origin behind a proxy; the API lives under /api (no CORS).
api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(auth.router)
v1_router.include_router(invitations.router)
api_router.include_router(v1_router)
