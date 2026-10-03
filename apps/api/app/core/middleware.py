"""Request id and Origin checking.

RequestIdMiddleware gives every request a fresh id (never one taken from the client) used in the
problem+json, the X-Request-ID header and the database setting app.request_id (audit).

OriginCheckMiddleware rejects every state-changing request whose Origin (or, failing that, Referer)
is not one of the site's own origins: the CSRF defence that also covers the pre-login routes, where
there is no session to bind a token to (login CSRF). Routes that legitimately receive calls without
a browser (webhooks, later) must be listed in ORIGIN_EXEMPT_PATHS and authenticate by a secret.
"""

import uuid
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.auth.csrf import SAFE_METHODS
from app.core.errors import ProblemError, problem_response

ORIGIN_EXEMPT_PATHS: frozenset[str] = frozenset()


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response


def _origin_of(value: str | None) -> str | None:
    if not value:
        return None
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


class OriginCheckMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        allowed_origins: tuple[str, ...],
        exempt_paths: frozenset[str] = ORIGIN_EXEMPT_PATHS,
    ) -> None:
        super().__init__(app)
        self.allowed = frozenset(origin.lower() for origin in allowed_origins)
        self.exempt = exempt_paths

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method not in SAFE_METHODS and request.url.path not in self.exempt:
            origin = _origin_of(request.headers.get("origin")) or _origin_of(
                request.headers.get("referer")
            )
            # "Origin: null" (sandboxed pages) and a missing header both end up here.
            if origin is None or origin not in self.allowed:
                return problem_response(
                    request,
                    ProblemError(
                        403, "origin_not_allowed", "This request does not come from the site"
                    ),
                )
        return await call_next(request)
