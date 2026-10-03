import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, uuid_pk
from app.models.organization import SLUG_PATTERN


class School(TimestampMixin, Base):
    __tablename__ = "schools"
    __table_args__ = (
        # slug is globally unique: the public portal resolves a school by /apm/{slug} (ADR-010).
        UniqueConstraint("slug"),
        # Target of the composite foreign keys (school_id, organization_id).
        UniqueConstraint("id", "organization_id", name="uq_schools_id_organization_id"),
        CheckConstraint(f"slug ~ '{SLUG_PATTERN}'", name="slug_format"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        Index("ix_schools_organization_id", "organization_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(Text)
