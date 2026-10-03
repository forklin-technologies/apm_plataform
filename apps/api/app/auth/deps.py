"""FastAPI dependencies: the request's database session, the principal, and the route markers.

The flow for an authenticated request, in this order, all inside ONE database session:
  cookie -> SHA-256 -> app.session_id -> the session row (not revoked, not expired, not idle)
  -> app.user_id and actor USER -> CSRF (for state-changing methods) -> the user is active
  -> the user's active memberships (a SECURITY DEFINER function) -> the session's membership is
  one of them (else the session is revoked) -> the tenant context comes from THAT membership.
The tenant is never read from a header, the query or the body.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import sessions as session_store
from app.auth.csrf import SAFE_METHODS, csrf_is_valid, csrf_token
from app.auth.identity import MembershipRow, list_memberships
from app.auth.permissions import (
    AuthenticatedMarker,
    Permission,
    PermissionRequirement,
    PublicMarker,
    permissions_for,
)
from app.auth.tokens import hash_token, looks_like_a_token
from app.core.config import ApiSettings
from app.core.errors import ProblemError
from app.db.request_context import bind_request_context
from app.db.tenant import TenantContext, bind_tenant


def get_api_settings(request: Request) -> ApiSettings:
    settings: ApiSettings = request.app.state.settings
    return settings


def get_request_db(request: Request) -> Iterator[Session]:
    """One database session per request, carrying the request id from the first statement on."""
    with request.app.state.session_factory() as session:
        bind_request_context(session, request_id=request.state.request_id)
        try:
            yield session
        except BaseException:
            session.rollback()
            raise


Db = Annotated[Session, Depends(get_request_db)]
Settings = Annotated[ApiSettings, Depends(get_api_settings)]


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    email: str
    full_name: str
    session_id: bytes
    expires_at: datetime
    memberships: list[MembershipRow]
    active: MembershipRow | None
    csrf_token: str

    @property
    def role(self) -> str | None:
        return None if self.active is None else self.active.role

    @property
    def tenant(self) -> TenantContext | None:
        if self.active is None:
            return None
        return TenantContext(self.active.organization_id, self.active.school_id)


def _unauthenticated(
    code: str = "unauthenticated", title: str = "Authentication required"
) -> ProblemError:
    return ProblemError(401, code, title, clear_session=True)


def _load_principal(request: Request, db: Session, settings: ApiSettings) -> Principal:
    token = request.cookies.get(settings.session_cookie_name)
    if not token or not looks_like_a_token(token):
        raise _unauthenticated()
    session_id = hash_token(token)
    bind_request_context(db, session_id=session_id)

    row = session_store.load_session(db, session_id)
    if row is None:
        raise _unauthenticated()
    bind_request_context(db, user_id=row.user_id, actor_type="USER")

    if request.method not in SAFE_METHODS and not csrf_is_valid(
        request, settings.auth_secret, session_id, settings.csrf_cookie_name
    ):
        raise ProblemError(403, "csrf_failed", "CSRF token missing or invalid")

    user = db.execute(
        text("SELECT email, full_name, is_active FROM users WHERE id = :id"), {"id": row.user_id}
    ).one_or_none()
    if user is None or not user[2]:
        session_store.revoke_session(db, session_id, "user_inactive")
        db.commit()
        raise _unauthenticated("session_revoked", "The session is no longer valid")

    memberships = list_memberships(db, row.user_id)
    active: MembershipRow | None = None
    if row.membership_id is not None:
        active = next((m for m in memberships if m.membership_id == row.membership_id), None)
        if active is None:
            # The membership was revoked or suspended after the session began: end it right now.
            session_store.revoke_session(db, session_id, "membership_inactive")
            db.commit()
            raise _unauthenticated("session_revoked", "The session is no longer valid")

    session_store.touch_session(db, session_id)
    db.commit()
    if active is not None:
        bind_tenant(db, TenantContext(active.organization_id, active.school_id))
    return Principal(
        user_id=row.user_id,
        email=user[0],
        full_name=user[1],
        session_id=session_id,
        expires_at=row.expires_at,
        memberships=memberships,
        active=active,
        csrf_token=csrf_token(settings.auth_secret, session_id),
    )


def get_principal(request: Request, db: Db, settings: Settings) -> Principal:
    principal = _load_principal(request, db, settings)
    request.state.principal = principal
    return principal


def get_optional_principal(request: Request, db: Db, settings: Settings) -> Principal | None:
    """For the routes that work with or without a session (logout, accepting an invitation)."""
    if not request.cookies.get(settings.session_cookie_name):
        return None
    try:
        return get_principal(request, db, settings)
    except ProblemError as error:
        if error.status == 403:  # a session with a bad CSRF token is an attack, not "no session"
            raise
        return None


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
OptionalPrincipal = Annotated[Principal | None, Depends(get_optional_principal)]


class _Require(PermissionRequirement):
    def __call__(self, principal: CurrentPrincipal) -> Principal:
        if principal.active is None:
            raise ProblemError(
                409, "context_required", "Choose which school or organization to act for first"
            )
        if self.permission not in permissions_for(principal.role):
            raise ProblemError(403, "permission_denied", "You are not allowed to do this")
        return principal


class _Authenticated(AuthenticatedMarker):
    def __call__(self, principal: CurrentPrincipal) -> Principal:
        return principal


class _Public(PublicMarker):
    def __call__(self) -> None:
        return None


def require(permission: Permission) -> _Require:
    """Declare that a route needs an authenticated user whose role holds `permission`."""
    return _Require(permission)


def authenticated() -> _Authenticated:
    """Declare that a route needs a session, whatever the role (it is about the user themself)."""
    return _Authenticated()


def public() -> _Public:
    """Declare that a route needs no session. It must be listed in PUBLIC_ROUTES."""
    return _Public()
