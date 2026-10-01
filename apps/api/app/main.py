from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.db.session import build_engine, build_session_factory
from app.routers.api import api_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = build_engine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(
        title="APM Digital API",
        lifespan=lifespan,
        openapi_url="/api/openapi.json",
        docs_url=None if settings.is_production else "/api/docs",
        redoc_url=None if settings.is_production else "/api/redoc",
    )
    app.state.session_factory = build_session_factory(engine)
    app.include_router(api_router)
    return app
