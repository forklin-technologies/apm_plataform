"""Per-request settings the database needs besides the tenant (ADR-016, ADR-015).

Four transaction-local settings, applied with `set_config(..., true)` like the tenant context, so
they end with the transaction and never reach the next user of a pooled connection:

- `app.session_id`   hex SHA-256 of the session token: lets row level security show THAT session row
- `app.user_id`      the authenticated user, set only after the session was validated (or after the
                     password was verified at login); it scopes the user's own sessions and row
- `app.request_id`   the id of the request (the one in the problem+json); read by audit triggers
- `app.actor_type`   USER (authenticated session), PUBLIC (anonymous flow) or SYSTEM (a job);
                     read by
                     audit triggers, so it must be set explicitly, never defaulted

Each value can be set once per Session (binding a different value raises, like `bind_tenant`): a
session never changes who it acts for midway.
"""

import re
import uuid
from dataclasses import dataclass, replace
from typing import Any, Literal

from sqlalchemy import Connection, event, text
from sqlalchemy.orm import Session, SessionTransaction

from app.db.tenant import TenantContextConflict

ActorType = Literal["USER", "PUBLIC", "SYSTEM"]
ACTOR_TYPES: tuple[ActorType, ...] = ("USER", "PUBLIC", "SYSTEM")

SESSION_SETTING = "app.session_id"
USER_SETTING = "app.user_id"
REQUEST_SETTING = "app.request_id"
ACTOR_SETTING = "app.actor_type"

_KEY = "request_context"
_LISTENER_KEY = "request_context_listener"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

# Literal on purpose (the names above are the same): no SQL is ever built from a string.
_SET = text(
    "SELECT set_config('app.session_id', :session_id, true), "
    "set_config('app.user_id', :user_id, true), "
    "set_config('app.request_id', :request_id, true), "
    "set_config('app.actor_type', :actor_type, true)"
)


@dataclass(frozen=True)
class RequestContext:
    request_id: str | None = None
    actor_type: ActorType | None = None
    session_id: bytes | None = None
    user_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        if self.request_id is not None and not _REQUEST_ID.fullmatch(self.request_id):
            raise ValueError("request_id must be 8 to 64 characters of [A-Za-z0-9._-]")
        if self.actor_type is not None and self.actor_type not in ACTOR_TYPES:
            raise ValueError("actor_type must be USER, PUBLIC or SYSTEM")
        if self.session_id is not None and len(self.session_id) != 32:
            raise ValueError("session_id must be a SHA-256 (32 bytes)")
        if self.user_id is not None and not isinstance(self.user_id, uuid.UUID):
            raise TypeError("user_id must be a UUID")


def apply_request_context(connection: Connection, context: RequestContext) -> None:
    """Set the settings for the CURRENT transaction of this connection (values are bound)."""
    connection.execute(
        _SET,
        {
            "session_id": "" if context.session_id is None else context.session_id.hex(),
            "user_id": "" if context.user_id is None else str(context.user_id),
            "request_id": context.request_id or "",
            "actor_type": context.actor_type or "",
        },
    )


def _apply_on_begin(
    session: Session, _transaction: SessionTransaction, connection: Connection
) -> None:
    context = session.info.get(_KEY)
    if context is not None:
        apply_request_context(connection, context)


def bind_request_context(session: Session, **values: Any) -> RequestContext:
    """Add settings to this Session; each one may be set once (the same value again is a no-op)."""
    current: RequestContext = session.info.get(_KEY) or RequestContext()
    for field, value in values.items():
        if value is None:
            continue
        existing = getattr(current, field)
        if existing is not None and existing != value:
            raise TenantContextConflict(f"this session is already bound to another {field}")
    merged = replace(current, **{k: v for k, v in values.items() if v is not None})
    if merged == current:
        return current
    if session.in_nested_transaction():
        raise TenantContextConflict("bind the request context before opening a savepoint")
    session.info[_KEY] = merged
    if not session.info.get(_LISTENER_KEY):
        session.info[_LISTENER_KEY] = True
        event.listen(session, "after_begin", _apply_on_begin)
    if session.in_transaction():
        apply_request_context(session.connection(), merged)
    return merged
