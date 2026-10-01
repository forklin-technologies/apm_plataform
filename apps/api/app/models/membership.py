import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, uuid_pk

# platform_admin is NOT here on purpose: a platform role does not belong to an organization
# (ADR-014). The permission matrix per role lives in code, not in tables (TASK-004).
ROLES = ("organization_admin", "school_admin", "treasurer", "staff", "viewer")
STATUSES = ("invited", "active", "suspended", "revoked")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class Membership(TimestampMixin, Base):
    """What a user may do in a tenant. school_id NULL means the whole organization."""

    __tablename__ = "memberships"
    __table_args__ = (
        # A row can never mix an organization with a school of another one. With school_id NULL
        # the constraint is not checked (MATCH SIMPLE), which is how org-wide access is stored.
        ForeignKeyConstraint(
            ["school_id", "organization_id"],
            ["schools.id", "schools.organization_id"],
            ondelete="RESTRICT",
            name="fk_memberships_school_id_organization_id_schools",
        ),
        CheckConstraint(_in_list("role", ROLES), name="role_valid"),
        CheckConstraint(_in_list("status", STATUSES), name="status_valid"),
        CheckConstraint(
            "role <> 'organization_admin' OR school_id IS NULL",
            name="organization_admin_is_org_wide",
        ),
        Index(
            "uq_memberships_user_org_school",
            "user_id",
            "organization_id",
            "school_id",
            unique=True,
            postgresql_where=text("school_id IS NOT NULL"),
        ),
        Index(
            "uq_memberships_user_org_wide",
            "user_id",
            "organization_id",
            unique=True,
            postgresql_where=text("school_id IS NULL"),
        ),
        Index("ix_memberships_user_id", "user_id"),
        Index("ix_memberships_organization_id", "organization_id"),
        Index("ix_memberships_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"))
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    school_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    role: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'invited'"))
