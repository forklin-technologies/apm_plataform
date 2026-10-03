from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings, get_settings
from app.db.posture import PostureError, assert_posture
from app.db.session import build_engine, build_session_factory
from app.routers.api import api_router


def create_app(settings: Settings | None = None, *, verify_posture: bool | None = None) -> FastAPI:
    """Build the app. Unless ENV=test, it refuses to START on a database whose posture is wrong."""
    settings = settings or get_settings()
    engine = build_engine(settings)
    verify = settings.env != "test" if verify_posture is None else verify_posture

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if verify:
            try:
                with engine.connect() as connection:
                    assert_posture(connection)
            except SQLAlchemyError:
                # Failing closed: serving without knowing whether row level security applies is
                # exactly what this check exists to prevent. Fixed text, the cause is dropped.
                raise PostureError(
                    "database posture check could not run: the database is unreachable"
                ) from None
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
