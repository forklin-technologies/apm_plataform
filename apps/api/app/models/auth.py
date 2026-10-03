"""Authentication tables (migration 0006). Not tenant data except `invitations`."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.membership import ROLES
from app.models.mixins import TimestampMixin, uuid_pk

SESSION_REVOKED_REASONS = (
    "logout",
    "rotated",
    "password_changed",
    "membership_inactive",
    "user_inactive",
)
ATTEMPT_KINDS = ("login", "password", "invitation")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class UserSession(Base):
    """A server-side session. `id` is the SHA-256 of the cookie token: knowing it authenticates
    nobody. membership_id is the ACTIVE membership (NULL until the user picks one)."""

    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("octet_length(id) = 32", name="id_is_a_sha256"),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        CheckConstraint(
            f"revoked_reason IS NULL OR {_in_list('revoked_reason', SESSION_REVOKED_REASONS)}",
            name="revoked_reason_valid",
        ),
        # A session can never point at a membership of ANOTHER user.
        ForeignKeyConstraint(
            ["membership_id", "user_id"],
            ["memberships.id", "memberships.user_id"],
            ondelete="RESTRICT",
            name="fk_sessions_membership_id_user_id_memberships",
        ),
        Index("ix_sessions_user_id", "user_id"),
        Index("ix_sessions_membership_id", "membership_id"),
    )

    id: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"))
    membership_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[Any | None] = mapped_column(INET)
    user_agent_hash: Mapped[bytes | None] = mapped_column(LargeBinary)


class LoginAttempt(Base):
    """One attempt, kept only as HMACs (never the e-mail or the IP), for progressive blocking."""

    __tablename__ = "login_attempts"
    __table_args__ = (
        CheckConstraint(_in_list("kind", ATTEMPT_KINDS), name="kind_valid"),
        CheckConstraint(
            "octet_length(subject_hmac) = 32 AND octet_length(ip_hmac) = 32",
            name="hmacs_are_sha256",
        ),
        Index("ix_login_attempts_pair", "kind", "subject_hmac", "ip_hmac", "attempted_at"),
        Index("ix_login_attempts_ip", "kind", "ip_hmac", "attempted_at"),
        Index("ix_login_attempts_subject", "kind", "subject_hmac", "attempted_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    subject_hmac: Mapped[bytes] = mapped_column(LargeBinary)
    ip_hmac: Mapped[bytes] = mapped_column(LargeBinary)
    succeeded: Mapped[bool] = mapped_column(Boolean)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Invitation(TimestampMixin, Base):
    """An invitation to join a tenant. The token is stored only as its SHA-256; single use."""

    __tablename__ = "invitations"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        CheckConstraint("octet_length(token_hash) = 32", name="token_hash_is_a_sha256"),
        CheckConstraint(
            r"email ~ '^[^@\s]+@[^@\s]+$' AND email = lower(email)", name="email_format"
        ),
        CheckConstraint(_in_list("role", ROLES), name="role_valid"),
        CheckConstraint(
            "role <> 'organization_admin' OR school_id IS NULL",
            name="organization_admin_is_org_wide",
        ),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        CheckConstraint(
            "(accepted_at IS NULL) = (accepted_user_id IS NULL)", name="accepted_has_a_user"
        ),
        ForeignKeyConstraint(
            ["school_id", "organization_id"],
            ["schools.id", "schools.organization_id"],
            ondelete="RESTRICT",
            name="fk_invitations_school_id_organization_id_schools",
        ),
        Index("ix_invitations_organization_id", "organization_id"),
        Index(
            "uq_invitations_live_email_scope",
            "organization_id",
            "email",
            text("COALESCE(school_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            unique=True,
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    school_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    email: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary)
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
