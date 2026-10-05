"""Cookies and response bodies shared by the auth routes."""

from datetime import UTC, datetime
from typing import Any

from fastapi import Response

from app.auth.csrf import csrf_token
from app.auth.identity import MembershipRow
from app.auth.permissions import permissions_for
from app.auth.sessions import IDLE_TIMEOUT_SECONDS
from app.core.config import ApiSettings
from app.core.errors import PROBLEM_MEDIA_TYPE
from app.schemas.auth import (
    ActiveMembershipOut,
    MembershipOut,
    OrganizationRef,
    SchoolRef,
    SessionInfo,
    SessionResponse,
    UserOut,
)
from app.schemas.problem import Problem

PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {
        "model": Problem,
        "description": description,
        "content": {PROBLEM_MEDIA_TYPE: {"schema": {"$ref": "#/components/schemas/Problem"}}},
    }
    for code, description in (
        (401, "unauthenticated, or the session was revoked or expired"),
        (403, "forbidden, csrf_failed or origin_not_allowed"),
        (404, "not_found (one answer for every reason: unknown, foreign, expired or malformed)"),
        (409, "conflict"),
        (422, "validation_error"),
        (429, "rate_limited (see Retry-After)"),
        (501, "provider_not_configured"),
    )
}


def problem_responses(
    *codes: int, extra: dict[int | str, dict[str, Any]] | None = None
) -> dict[int | str, dict[str, Any]]:
    """The OpenAPI `responses` for the given problem status codes."""
    chosen: dict[int | str, dict[str, Any]] = {code: PROBLEM_RESPONSES[code] for code in codes}
    chosen.update(extra or {})
    return chosen


def membership_out(row: MembershipRow) -> MembershipOut:
    return MembershipOut(
        membership_id=row.membership_id,
        organization=OrganizationRef(
            id=row.organization_id, name=row.organization_name, slug=row.organization_slug
        ),
        school=None
        if row.school_id is None
        else SchoolRef(id=row.school_id, name=row.school_name or "", slug=row.school_slug or ""),
        role=row.role,  # type: ignore[arg-type]
    )


def session_response(
    *,
    user_id: Any,
    email: str,
    full_name: str,
    memberships: list[MembershipRow],
    active: MembershipRow | None,
    session_id: bytes,
    expires_at: datetime,
    settings: ApiSettings,
) -> SessionResponse:
    active_out = None
    if active is not None:
        base = membership_out(active)
        permissions = sorted(permission.value for permission in permissions_for(active.role))
        active_out = ActiveMembershipOut(**base.model_dump(), permissions=permissions)
    return SessionResponse(
        user=UserOut(id=user_id, email=email, full_name=full_name),
        active_membership=active_out,
        memberships=[membership_out(row) for row in memberships],
        csrf_token=csrf_token(settings.auth_secret, session_id),
        session=SessionInfo(expires_at=expires_at, idle_timeout_seconds=IDLE_TIMEOUT_SECONDS),
    )


def set_session_cookies(
    response: Response, settings: ApiSettings, token: str, session_id: bytes, expires_at: datetime
) -> None:
    """The session cookie (httpOnly) and the CSRF cookie (readable by the page, same lifetime)."""
    max_age = max(60, int((expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=max_age,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf_token(settings.auth_secret, session_id),
        max_age=max_age,
        path="/",
        secure=settings.cookie_secure,
        httponly=False,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"


def clear_session_cookies(response: Response, settings: ApiSettings) -> None:
    for name, httponly in (
        (settings.session_cookie_name, True),
        (settings.csrf_cookie_name, False),
    ):
        response.delete_cookie(
            name, path="/", secure=settings.cookie_secure, httponly=httponly, samesite="lax"
        )
    response.headers["Cache-Control"] = "no-store"
