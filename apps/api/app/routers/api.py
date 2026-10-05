from fastapi import APIRouter

from app.contributions.router import router as contributions_router
from app.dev.router import router as dev_router
from app.pix.webhook import router as webhook_router
from app.public.router import router as public_router
from app.routers import health
from app.routers.v1 import auth, invitations

# Web and API share one origin behind a proxy; the API lives under /api (no CORS).
api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(auth.router)
v1_router.include_router(invitations.router)
v1_router.include_router(public_router)
v1_router.include_router(webhook_router)
v1_router.include_router(contributions_router)
v1_router.include_router(dev_router)
api_router.include_router(v1_router)
