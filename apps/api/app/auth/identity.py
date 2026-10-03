"""The three SECURITY DEFINER functions of ADR-016, called by name with bound parameters.

They are the only way the application reads a user by e-mail, lists the memberships of a user, or
accepts an invitation, because none of those can happen inside a tenant context (there is none yet).
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class LoginIdentity:
    user_id: UUID
    password_hash: str | None
    is_active: bool


@dataclass(frozen=True)
class MembershipRow:
    membership_id: UUID
    organization_id: UUID
    organization_name: str
    organization_slug: str
    school_id: UUID | None
    school_name: str | None
    school_slug: str | None
    role: str


@dataclass(frozen=True)
class AcceptResult:
    outcome: str
    user_id: UUID | None
    membership_id: UUID | None
    organization_id: UUID | None
    school_id: UUID | None
    role: str | None


def find_login_identity(db: Session, email: str) -> LoginIdentity | None:
    row = db.execute(
        text("SELECT user_id, password_hash, is_active FROM public.find_login_identity(:email)"),
        {"email": email},
    ).one_or_none()
    return None if row is None else LoginIdentity(row[0], row[1], row[2])


def list_memberships(db: Session, user_id: UUID) -> list[MembershipRow]:
    rows = db.execute(
        text(
            "SELECT membership_id, organization_id, organization_name, organization_slug, "
            "school_id, school_name, school_slug, role "
            "FROM public.list_memberships_for_user(:user_id)"
        ),
        {"user_id": user_id},
    ).all()
    return [MembershipRow(*row) for row in rows]


def accept_invitation(
    db: Session,
    token_hash: bytes,
    full_name: str | None,
    password_hash: str | None,
    existing_user_id: UUID | None,
) -> AcceptResult:
    row = db.execute(
        text(
            "SELECT outcome, user_id, membership_id, organization_id, school_id, role "
            "FROM public.accept_invitation(:token_hash, :full_name, :password_hash, :existing)"
        ),
        {
            "token_hash": token_hash,
            "full_name": full_name,
            "password_hash": password_hash,
            "existing": existing_user_id,
        },
    ).one()
    return AcceptResult(*row)
