import uuid

from sqlalchemy import Boolean, CheckConstraint, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, uuid_pk


class User(TimestampMixin, Base):
    """Global identity. Access to a tenant comes from `memberships`, not from this table."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(r"email ~ '^[^@\s]+@[^@\s]+$'", name="email_format"),
        CheckConstraint("length(btrim(full_name)) > 0", name="full_name_not_blank"),
        # E-mail is unique ignoring case; the original casing is kept as typed.
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text)
    # The application role has no SELECT on this column until authentication exists (TASK-004),
    # so it is deferred: session.get(User, id) must never select it.
    password_hash: Mapped[str | None] = mapped_column(Text, deferred=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
