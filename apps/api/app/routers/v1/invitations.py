"""POST /invitations (administrators) and POST /invitations/accept (the invitee)."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.auth import passwords
from app.auth.deps import Db, OptionalPrincipal, Principal, Settings, public, require
from app.auth.emailer import build_sender, invitation_message
from app.auth.identity import accept_invitation, list_memberships
from app.auth.permissions import INVITABLE_ROLES, Permission
from app.auth.ratelimit import attempt_keys, blocked_for, client_ip, record_attempt
from app.auth.responses import membership_out, problem_responses
from app.auth.tokens import hash_token, looks_like_a_token, new_token
from app.core.errors import ProblemError
from app.db.request_context import bind_request_context
from app.schemas.auth import (
    AcceptedInvitationOut,
    AcceptInvitationRequest,
    InvitationOut,
    InvitationRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/invitations", tags=["invitations"])

INVITATION_LIFETIME_HOURS = 72
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def _ip(request: Request) -> str:
    return client_ip(request.client.host if request.client else None)


def _invalid() -> ProblemError:
    # One answer for every reason: unknown, already used, revoked, expired, malformed.
    return ProblemError(400, "invitation_invalid", "This invitation is not valid")


def _field_error(field: str, code: str) -> ProblemError:
    return ProblemError(
        422, "validation_error", "Invalid request", errors=[{"field": field, "code": code}]
    )


@router.post(
    "",
    operation_id="invitations_create",
    summary="Invite someone to the active organization or school",
    status_code=201,
    response_model=InvitationOut,
    responses=problem_responses(401, 403, 409, 422),
    openapi_extra={"x-permission": Permission.INVITATIONS_CREATE.value},
)
def create_invitation(
    body: InvitationRequest,
    principal: Annotated[Principal, Depends(require(Permission.INVITATIONS_CREATE))],
    db: Db,
    settings: Settings,
) -> InvitationOut:
    """The tenant is the one of the ACTIVE membership; nothing about it comes from the body. An
    organization admin may invite any role; a school admin only for schools, and never an
    organization admin. The token travels by e-mail and is never in the response."""
    active = principal.active
    if active is None:  # require() already guarantees it; never rely on an assert for this
        raise ProblemError(
            409, "context_required", "Choose which school or organization to act for"
        )
    if body.role not in INVITABLE_ROLES.get(active.role, frozenset()):
        raise ProblemError(403, "permission_denied", "You may not invite that role")

    school_id: UUID | None = body.school_id
    if body.role == "organization_admin":
        if school_id is not None:
            raise _field_error("school_id", "must_be_empty_for_organization_admin")
    else:
        if active.school_id is not None:  # a school-scoped inviter can only invite into its school
            if school_id not in (None, active.school_id):
                raise ProblemError(403, "permission_denied", "You may only invite into your school")
            school_id = active.school_id
        if active.role == "school_admin" and school_id is None:
            raise _field_error("school_id", "required")

    if school_id is not None:
        # Visible only if it belongs to the active organization (and school): row level security.
        found = db.execute(text("SELECT 1 FROM schools WHERE id = :id"), {"id": school_id}).first()
        if found is None:
            raise _field_error("school_id", "not_found")

    # An existing member of the SAME scope is visible to this tenant (never one of another tenant).
    already = db.execute(
        text(
            "SELECT 1 FROM memberships m JOIN users u ON u.id = m.user_id "
            "WHERE lower(u.email) = :email AND m.school_id IS NOT DISTINCT FROM :school "
            "AND m.status IN ('active', 'invited', 'suspended')"
        ),
        {"email": body.email, "school": school_id},
    ).first()
    if already is not None:
        raise ProblemError(409, "already_member", "That person already has access here")

    # An expired invitation still occupies the "one live invitation per scope" slot: free it.
    db.execute(
        text(
            "UPDATE invitations SET revoked_at = now(), updated_at = now() "
            "WHERE email = :email AND COALESCE(school_id, CAST(:zero AS uuid)) = "
            "COALESCE(CAST(:school AS uuid), CAST(:zero AS uuid)) "
            "AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at <= now()"
        ),
        {"email": body.email, "school": school_id, "zero": _ZERO_UUID},
    )

    token = new_token()
    try:
        row = db.execute(
            text(
                "INSERT INTO invitations (organization_id, school_id, email, role, token_hash, "
                "invited_by_user_id, expires_at) VALUES (:org, :school, :email, :role, :hash, "
                ":by, now() + make_interval(hours => :hours)) "
                "RETURNING id, expires_at"
            ),
            {
                "org": active.organization_id,
                "school": school_id,
                "email": body.email,
                "role": body.role,
                "hash": hash_token(token),
                "by": principal.user_id,
                "hours": INVITATION_LIFETIME_HOURS,
            },
        ).one()
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ProblemError(
            409, "invitation_pending", "There is already a pending invitation for that person here"
        ) from None
    try:
        build_sender(settings).send(
            invitation_message(settings, body.email, token, active.organization_name)
        )
    except OSError:
        logger.warning("could not write the invitation e-mail to the outbox")
    return InvitationOut(
        id=row[0], email=body.email, role=body.role, school_id=school_id, expires_at=row[1]
    )


@router.post(
    "/accept",
    operation_id="invitations_accept",
    summary="Accept an invitation",
    status_code=201,
    response_model=AcceptedInvitationOut,
    responses=problem_responses(
        403,
        409,
        422,
        429,
        extra={400: {"description": "invitation_invalid (one answer for every reason)"}},
    ),
    dependencies=[Depends(public())],
)
def accept(
    body: AcceptInvitationRequest,
    request: Request,
    principal: OptionalPrincipal,
    db: Db,
    settings: Settings,
) -> AcceptedInvitationOut:
    """The token is the credential. A NEW person sends full_name and password and gets an account;
    someone who already has one must be LOGGED IN (the token alone never lets anybody act as an
    existing account) and sends only the token. Every failure looks the same."""
    bind_request_context(db, actor_type="PUBLIC" if principal is None else None)
    ip = _ip(request)
    # Keyed by IP alone: guessing tokens must not be able to lock other people out.
    keys = attempt_keys(settings.auth_secret, ip, ip)
    wait = blocked_for(db, "invitation", keys)
    if wait:
        db.commit()
        raise ProblemError(
            429,
            "rate_limited",
            "Too many attempts, try again later",
            headers={"Retry-After": str(wait)},
        )

    if principal is None and body.password is not None:
        problem = passwords.password_problem(body.password)
        if problem is not None:
            db.commit()
            raise ProblemError(
                422,
                "weak_password",
                "The password is not acceptable",
                errors=[{"field": "password", "code": problem}],
            )
    password_hash = (
        passwords.hash_password(body.password) if principal is None and body.password else None
    )

    result = None
    if looks_like_a_token(body.token):
        result = accept_invitation(
            db,
            hash_token(body.token),
            body.full_name,
            password_hash,
            None if principal is None else principal.user_id,
        )
    succeeded = result is not None and result.outcome.startswith("accepted_")
    record_attempt(db, "invitation", keys, succeeded=succeeded)
    if result is None or result.outcome == "invalid":
        db.commit()
        raise _invalid()
    if result.outcome == "login_required":
        db.commit()
        raise ProblemError(
            409,
            "account_exists_login_required",
            "That e-mail already has an account: log in and accept the invitation again",
        )
    if result.user_id is None or result.membership_id is None:
        raise _invalid()
    memberships = list_memberships(db, result.user_id)
    db.commit()
    created = next((m for m in memberships if m.membership_id == result.membership_id), None)
    if created is None:  # cannot happen (just created, active), but never answer with a guess
        raise _invalid()
    return AcceptedInvitationOut(user_id=result.user_id, membership=membership_out(created))
