"""Tenant context for row level security (ADR-014).

The database reads the tenant from two transaction-local settings, `app.organization_id` and
`app.school_id`. They are set with `set_config(..., true)`, which lasts until the transaction ends,
so a pooled connection never carries a context into the next transaction. The context is always
passed in explicitly: it must never be built from request data (header, query, body).

A Session bound with `bind_tenant` re-applies its context at the start of every transaction, so
`session.commit()` followed by more work keeps working; a Session that was never bound starts each
transaction with no context and, with RLS failing closed, sees and writes nothing.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Connection, event, text
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

ORGANIZATION_SETTING = "app.organization_id"
SCHOOL_SETTING = "app.school_id"

_CONTEXT_KEY = "tenant_context"
_LISTENER_KEY = "tenant_context_listener"

_SET_CONTEXT = text(
    f"SELECT set_config('{ORGANIZATION_SETTING}', :organization_id, true), "
    f"set_config('{SCHOOL_SETTING}', :school_id, true)"
)


@dataclass(frozen=True)
class TenantContext:
    """The organization, and optionally one of its schools, a transaction acts for.

    school_id None is an organization-wide context (every school of the organization).
    """

    organization_id: uuid.UUID
    school_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.organization_id, uuid.UUID):
            raise TypeError("organization_id must be a UUID")
        if self.school_id is not None and not isinstance(self.school_id, uuid.UUID):
            raise TypeError("school_id must be a UUID or None")


def apply_tenant_context(connection: Connection, context: TenantContext) -> None:
    """Set the context for the CURRENT transaction of this connection (values are bound)."""
    connection.execute(
        _SET_CONTEXT,
        {
            "organization_id": str(context.organization_id),
            # An empty string reads back as NULL (see public.app_school()).
            "school_id": "" if context.school_id is None else str(context.school_id),
        },
    )


def _apply_on_begin(session: Session, _transaction: SessionTransaction, connection: Connection) -> None:
    context = session.info.get(_CONTEXT_KEY)
    if context is not None:
        apply_tenant_context(connection, context)


def bind_tenant(session: Session, context: TenantContext) -> None:
    """Make every transaction of this session run with `context`."""
    session.info[_CONTEXT_KEY] = context
    if not session.info.get(_LISTENER_KEY):
        session.info[_LISTENER_KEY] = True
        event.listen(session, "after_begin", _apply_on_begin)
    if session.in_transaction():
        apply_tenant_context(session.connection(), context)


@contextmanager
def tenant_session(factory: sessionmaker[Session], context: TenantContext) -> Iterator[Session]:
    """A session whose transactions all carry `context`. Commits on success, rolls back on error."""
    with factory() as session:
        bind_tenant(session, context)
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
