"""Session rows. Every query relies on the row level security of `sessions`: a row is visible only
while app.session_id names it, or app.user_id is its user (see app/db/request_context.py)."""

import ipaddress
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

IDLE_TIMEOUT_SECONDS = 30 * 60
ABSOLUTE_LIFETIME_SECONDS = 12 * 60 * 60
TOUCH_AFTER_SECONDS = 60


@dataclass(frozen=True)
class SessionRow:
    user_id: UUID
    membership_id: UUID | None
    expires_at: datetime


def load_session(db: Session, session_id: bytes) -> SessionRow | None:
    """The session if it exists, is not revoked, not past its absolute end and not idle too long."""
    row = db.execute(
        text(
            "SELECT user_id, membership_id, expires_at FROM sessions "
            "WHERE id = :id AND revoked_at IS NULL AND expires_at > now() "
            "AND last_seen_at > now() - make_interval(secs => :idle)"
        ),
        {"id": session_id, "idle": IDLE_TIMEOUT_SECONDS},
    ).one_or_none()
    return None if row is None else SessionRow(row[0], row[1], row[2])


def _ip_or_none(value: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address(value or ""))
    except ValueError:
        return None


def create_session(
    db: Session,
    *,
    session_id: bytes,
    user_id: UUID,
    membership_id: UUID | None,
    ip: str | None,
    user_agent_hash: bytes | None,
    expires_at: datetime | None = None,
) -> datetime:
    """Insert a session and return its absolute expiry. A rotation passes the old `expires_at`
    so that switching context never extends how long a login can last."""
    params: dict[str, Any] = {
        "id": session_id,
        "user_id": user_id,
        "membership_id": membership_id,
        "ip": _ip_or_none(ip),
        "ua": user_agent_hash,
        "lifetime": ABSOLUTE_LIFETIME_SECONDS,
        "expires_at": expires_at,
    }
    created: datetime = db.execute(
        text(
            "INSERT INTO sessions (id, user_id, membership_id, expires_at, ip, user_agent_hash) "
            "VALUES (:id, :user_id, :membership_id, "
            "COALESCE(:expires_at, now() + make_interval(secs => :lifetime)), "
            "CAST(:ip AS inet), :ua) "
            "RETURNING expires_at"
        ),
        params,
    ).scalar_one()
    return created


def touch_session(db: Session, session_id: bytes) -> None:
    db.execute(
        text(
            "UPDATE sessions SET last_seen_at = now() "
            "WHERE id = :id AND last_seen_at < now() - make_interval(secs => :after)"
        ),
        {"id": session_id, "after": TOUCH_AFTER_SECONDS},
    )


def revoke_session(db: Session, session_id: bytes, reason: str) -> None:
    db.execute(
        text(
            "UPDATE sessions SET revoked_at = now(), revoked_reason = :reason "
            "WHERE id = :id AND revoked_at IS NULL"
        ),
        {"id": session_id, "reason": reason},
    )


def revoke_all_sessions(db: Session, user_id: UUID, reason: str) -> None:
    db.execute(
        text(
            "UPDATE sessions SET revoked_at = now(), revoked_reason = :reason "
            "WHERE user_id = :user_id AND revoked_at IS NULL"
        ),
        {"user_id": user_id, "reason": reason},
    )
