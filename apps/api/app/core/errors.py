"""Errors as RFC 9457 problem+json with a STABLE `code` (the contract the web client switches on).

Nothing here ever puts a request value in a response: validation errors carry the field and the
kind of error, never the input (it may be a password), and unexpected errors carry no detail.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import ApiSettings

logger = logging.getLogger(__name__)

PROBLEM_MEDIA_TYPE = "application/problem+json"
PROBLEM_TYPE_PREFIX = "urn:apm-digital:problem:"


class ProblemError(Exception):
    """Raise from a route or dependency to answer with a problem."""

    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        *,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
        clear_session: bool = False,
        errors: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.headers = headers or {}
        self.clear_session = clear_session
        self.errors = errors


def request_id_of(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def problem_response(request: Request, error: ProblemError) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"{PROBLEM_TYPE_PREFIX}{error.code}",
        "title": error.title,
        "status": error.status,
        "code": error.code,
        "request_id": request_id_of(request),
    }
    if error.detail:
        body["detail"] = error.detail
    if error.errors:
        body["errors"] = error.errors
    response = JSONResponse(
        body, status_code=error.status, headers=error.headers, media_type=PROBLEM_MEDIA_TYPE
    )
    if error.clear_session:
        settings: ApiSettings = request.app.state.settings
        for name in (settings.session_cookie_name, settings.csrf_cookie_name):
            response.delete_cookie(
                name,
                path="/",
                secure=settings.cookie_secure,
                samesite="lax",
                httponly=name == settings.session_cookie_name,
            )
    response.headers["Cache-Control"] = "no-store"
    return response


_HTTP_CODES = {
    400: ("bad_request", "Bad request"),
    404: ("not_found", "Not found"),
    405: ("method_not_allowed", "Method not allowed"),
    413: ("payload_too_large", "Payload too large"),
    415: ("unsupported_media_type", "Unsupported media type"),
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(request: Request, error: ProblemError) -> JSONResponse:
        return problem_response(request, error)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, error: RequestValidationError) -> JSONResponse:
        # Field and kind only: `input` and `msg` can quote what the client sent (a password).
        errors = [
            {
                "field": ".".join(str(part) for part in item["loc"] if part != "body"),
                "code": item["type"],
            }
            for item in error.errors()
        ]
        return problem_response(
            request, ProblemError(422, "validation_error", "Invalid request", errors=errors)
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, error: StarletteHTTPException) -> JSONResponse:
        code, title = _HTTP_CODES.get(error.status_code, ("http_error", "Request failed"))
        return problem_response(
            request, ProblemError(error.status_code, code, title, headers=dict(error.headers or {}))
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, error: Exception) -> JSONResponse:
        # Server side only, with the request id to correlate; the client learns nothing.
        logger.error(
            "unhandled error on request %s: %s", request_id_of(request), type(error).__name__
        )
        return problem_response(request, ProblemError(500, "internal_error", "Internal error"))
