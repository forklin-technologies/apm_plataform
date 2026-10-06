from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from app.auth.emailer import build_sender
from app.closing.routes import router as closing_router
from app.core.config import ApiSettings, get_api_settings
from app.core.errors import install_error_handlers
from app.core.middleware import OriginCheckMiddleware, RequestIdMiddleware
from app.db.posture import PostureError, assert_posture
from app.db.session import build_engine, build_session_factory
from app.expenses.router import router as expenses_router
from app.reports.routes import router as reports_router
from app.routers.api import api_router
from app.statement.routes import router as statement_router


def create_app(
    settings: ApiSettings | None = None, *, verify_posture: bool | None = None
) -> FastAPI:
    """Build the app. Unless ENV=test, it refuses to START on a database whose posture is wrong."""
    settings = settings or get_api_settings()
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
        version="1.0.0",
        lifespan=lifespan,
        openapi_url="/api/openapi.json",
        docs_url=None if settings.is_production else "/api/docs",
        redoc_url=None if settings.is_production else "/api/redoc",
        swagger_ui_oauth2_redirect_url=None,  # no OAuth here: no extra route outside /api
    )
    app.state.settings = settings
    app.state.session_factory = build_session_factory(engine)
    app.state.email_sender = build_sender(settings)
    install_error_handlers(app)
    # Added last = outermost: the request id exists before the Origin check can answer with it.
    app.add_middleware(OriginCheckMiddleware, allowed_origins=settings.allowed_origins)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(api_router)
    app.include_router(expenses_router, prefix="/api/v1")  # TASK-007: expenses
    app.include_router(statement_router)
    app.include_router(closing_router)
    app.include_router(reports_router)
    _document_security(app, settings)
    return app


def _document_security(app: FastAPI, settings: ApiSettings) -> None:
    """Describe the cookie session and the CSRF header in the OpenAPI document."""
    original = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = original()
        schema.setdefault("components", {}).setdefault("securitySchemes", {}).update(
            {
                "sessionCookie": {
                    "type": "apiKey",
                    "in": "cookie",
                    "name": settings.session_cookie_name,
                    "description": "Session cookie set by POST /api/v1/auth/login (httpOnly).",
                },
                "csrfHeader": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-CSRF-Token",
                    "description": "Required on every state-changing request of a logged-in "
                    "user; its value is the `csrf_token` of the session response and the "
                    "readable CSRF cookie. An Origin of the site is also required.",
                },
            }
        )
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
