"""Support tables of the financial schema: categories, settings, Pix, webhooks, audit, closings."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    FetchedValue,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.financial_common import (
    HEX64,
    ScopeMixin,
    fin_pk,
    scope_constraints,
    user_fk,
)
from app.models.mixins import TimestampMixin

PROVIDERS = ("BB", "SANDBOX")


class Category(ScopeMixin, TimestampMixin, Base):
    """A category of a school, applicable to money in (IN) or out (OUT), never both."""

    __tablename__ = "categories"
    __table_args__ = (
        *scope_constraints("categories"),
        UniqueConstraint("school_id", "key", name="uq_categories_school_id_key"),
        # Target of financial_transactions (category_id, direction, school_id, organization_id).
        UniqueConstraint(
            "id",
            "applies_to",
            "school_id",
            "organization_id",
            name="uq_categories_id_applies_to_school_id_organization_id",
        ),
        CheckConstraint("key ~ '^[a-z0-9_]{1,40}$'", name="key_format"),
        CheckConstraint("length(btrim(name)) BETWEEN 1 AND 80", name="name_length"),
        CheckConstraint("applies_to IN ('IN', 'OUT')", name="applies_to_valid"),
        Index("ix_categories_school_id", "school_id"),
        Index("ix_categories_organization_id", "organization_id"),
    )

    id: Mapped[uuid.UUID] = fin_pk()
    key: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    applies_to: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class SchoolSettings(ScopeMixin, TimestampMixin, Base):
    """One row per school (1:1), created by the database with the school."""

    __tablename__ = "school_settings"
    __table_args__ = (
        *scope_constraints("school_settings"),
        UniqueConstraint(
            "school_id", "organization_id", name="uq_school_settings_school_id_organization_id"
        ),
        CheckConstraint("(now() AT TIME ZONE timezone) IS NOT NULL", name="timezone_valid"),
        CheckConstraint(
            "min_contribution_cents > 0 AND min_contribution_cents <= max_contribution_cents "
            "AND max_contribution_cents <= 1000000000",
            name="contribution_range",
        ),
        CheckConstraint("pix_expiration_minutes BETWEEN 5 AND 1440", name="pix_expiration_range"),
        CheckConstraint(
            "identification_mode IN ('ANONYMOUS', 'OPTIONAL', 'REQUIRED')",
            name="identification_mode_valid",
        ),
        CheckConstraint(
            "required_fields <@ ARRAY['guardian_name', 'student_name', 'class_name']::text[] "
            "AND (identification_mode <> 'ANONYMOUS' OR cardinality(required_fields) = 0) "
            "AND (identification_mode <> 'REQUIRED' OR cardinality(required_fields) >= 1)",
            name="required_fields_valid",
        ),
        CheckConstraint("brand_accent ~ '^#[0-9a-fA-F]{6}$'", name="brand_accent_format"),
        CheckConstraint(
            "brand_accent_contrast ~ '^#[0-9a-fA-F]{6}$'", name="brand_accent_contrast_format"
        ),
        CheckConstraint(
            "approval_limit_cents IS NULL OR approval_limit_cents > 0",
            name="approval_limit_positive",
        ),
        Index("ix_school_settings_organization_id", "organization_id"),
    )

    # school_id is the primary key (1:1 with the school); ScopeMixin declares the column.
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    timezone: Mapped[str] = mapped_column(Text, server_default=text("'America/Sao_Paulo'"))
    min_contribution_cents: Mapped[int] = mapped_column(BigInteger, server_default=text("1000"))
    max_contribution_cents: Mapped[int] = mapped_column(BigInteger, server_default=text("500000"))
    pix_expiration_minutes: Mapped[int] = mapped_column(Integer, server_default=text("30"))
    identification_mode: Mapped[str] = mapped_column(Text, server_default=text("'OPTIONAL'"))
    required_fields: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    brand_accent: Mapped[str] = mapped_column(Text, server_default=text("'#0a84ff'"))
    brand_accent_contrast: Mapped[str] = mapped_column(Text, server_default=text("'#ffffff'"))
    # NULL = off. An expense above it needs the organization administrator (rule in the service).
    approval_limit_cents: Mapped[int | None] = mapped_column(BigInteger)


class PixCharge(ScopeMixin, TimestampMixin, Base):
    """A dynamic Pix charge of a contribution. Only the provider's answer confirms it (ADR-011)."""

    __tablename__ = "pix_charges"
    __table_args__ = (
        *scope_constraints("pix_charges"),
        ForeignKeyConstraint(
            ["transaction_id", "organization_id", "school_id"],
            [
                "contributions.transaction_id",
                "contributions.organization_id",
                "contributions.school_id",
            ],
            name="fk_pix_charges_transaction_id_contributions",
            ondelete="RESTRICT",
        ),
        # Per school, never global (a global UNIQUE would be an oracle across tenants).
        UniqueConstraint(
            "school_id", "provider", "txid", name="uq_pix_charges_school_id_provider_txid"
        ),
        CheckConstraint("provider IN ('BB', 'SANDBOX')", name="provider_valid"),
        CheckConstraint("txid ~ '^[A-Za-z0-9]{26,35}$'", name="txid_format"),
        CheckConstraint(
            "status IN ('PENDING', 'PAID', 'EXPIRED', 'CANCELLED')", name="status_valid"
        ),
        CheckConstraint("amount_cents > 0 AND amount_cents <= 1000000000000", name="amount_range"),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        CheckConstraint(
            "end_to_end_id IS NULL OR end_to_end_id ~ '^E[A-Za-z0-9]{31}$'",
            name="end_to_end_id_format",
        ),
        CheckConstraint(
            "(status = 'PAID') = (end_to_end_id IS NOT NULL AND paid_at IS NOT NULL)",
            name="paid_iff_confirmed",
        ),
        CheckConstraint(
            "emv_payload IS NULL OR length(emv_payload) BETWEEN 1 AND 4096",
            name="emv_payload_length",
        ),
        Index(
            "uq_pix_charges_school_id_provider_end_to_end_id",
            "school_id",
            "provider",
            "end_to_end_id",
            unique=True,
            postgresql_where=text("end_to_end_id IS NOT NULL"),
        ),
        Index(
            "uq_pix_charges_one_pending_per_contribution",
            "school_id",
            "transaction_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index("ix_pix_charges_transaction_id", "transaction_id"),
        Index("ix_pix_charges_school_id", "school_id"),
        Index(
            "ix_pix_charges_pending_expiry",
            "expires_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    id: Mapped[uuid.UUID] = fin_pk()
    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    provider: Mapped[str] = mapped_column(Text)
    txid: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'PENDING'"))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The "Pix copia e cola" BR Code. Not secret, but only recorded as present in the audit log.
    emv_payload: Mapped[str | None] = mapped_column(Text)
    end_to_end_id: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookEvent(Base):
    """A provider notification, kept raw. Idempotent by UNIQUE; never trusted alone (ADR-011)."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_webhook_events_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["school_id", "organization_id"],
            ["schools.id", "schools.organization_id"],
            name="fk_webhook_events_school_id_organization_id_schools",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "school_id",
            "provider",
            "idempotency_key",
            name="uq_webhook_events_school_id_provider_idempotency_key",
        ),
        CheckConstraint("provider IN ('BB', 'SANDBOX')", name="provider_valid"),
        CheckConstraint("length(idempotency_key) BETWEEN 1 AND 200", name="idempotency_key_length"),
        CheckConstraint(
            "end_to_end_id IS NULL OR end_to_end_id ~ '^E[A-Za-z0-9]{31}$'",
            name="end_to_end_id_format",
        ),
        CheckConstraint(
            "jsonb_typeof(raw_payload) = 'object' AND pg_column_size(raw_payload) < 65536",
            name="raw_payload_object",
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint(
            "processing_error IS NULL OR length(processing_error) <= 1000",
            name="processing_error_length",
        ),
        Index("ix_webhook_events_school_id", "school_id"),
        Index(
            "ix_webhook_events_unprocessed",
            "received_at",
            postgresql_where=text("processed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = fin_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    provider: Mapped[str] = mapped_column(Text)
    # The provider's event id, or the end-to-end id when it sends none.
    idempotency_key: Mapped[str] = mapped_column(Text)
    end_to_end_id: Mapped[str | None] = mapped_column(Text)
    # May carry the payer's CPF and name: the Pix layer must minimise it before storing (docs).
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    signature_valid: Mapped[bool] = mapped_column(Boolean)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    processing_error: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    """Append-only. Written by triggers in the same transaction as the change (ADR-015, D7)."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_audit_logs_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["school_id", "organization_id"],
            ["schools.id", "schools.organization_id"],
            name="fk_audit_logs_school_id_organization_id_schools",
            ondelete="RESTRICT",
        ),
        user_fk("audit_logs", "actor_user_id"),
        CheckConstraint("actor_type IN ('USER', 'SYSTEM', 'PUBLIC')", name="actor_type_valid"),
        CheckConstraint(
            "(actor_type = 'USER') = (actor_user_id IS NOT NULL)", name="actor_matches_type"
        ),
        CheckConstraint(r"action ~ '^[a-z0-9_]+(\.[a-z0-9_]+)+$'", name="action_format"),
        CheckConstraint("entity_type ~ '^[a-z0-9_]+$'", name="entity_type_format"),
        CheckConstraint(
            "before_data IS NULL OR jsonb_typeof(before_data) = 'object'", name="before_data_object"
        ),
        CheckConstraint(
            "after_data IS NULL OR jsonb_typeof(after_data) = 'object'", name="after_data_object"
        ),
        CheckConstraint(
            "request_id IS NULL OR length(request_id) BETWEEN 1 AND 200", name="request_id_length"
        ),
        Index("ix_audit_logs_school_occurred", "school_id", "occurred_at"),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_audit_logs_organization_id", "organization_id"),
        Index(
            "ix_audit_logs_actor_user_id",
            "actor_user_id",
            postgresql_where=text("actor_user_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = fin_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    # NULL for an event of the organization itself.
    school_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # actor_user_id, actor_type, request_id, ip and occurred_at come from the transaction settings
    # app.user_id, app.actor_type, app.request_id and app.client_ip (trigger audit_logs_05_actor).
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, server_default=FetchedValue())
    actor_type: Mapped[str] = mapped_column(Text, server_default=FetchedValue())
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Never personal data: ids and non-personal columns only.
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(Text, server_default=FetchedValue())
    ip: Mapped[str | None] = mapped_column(INET, server_default=FetchedValue())
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MonthlyClosing(ScopeMixin, Base):
    """A closed month. Every figure and the hash are computed by the database (trigger)."""

    __tablename__ = "monthly_closings"
    __table_args__ = (
        *scope_constraints("monthly_closings"),
        user_fk("monthly_closings", "closed_by_user_id"),
        user_fk("monthly_closings", "reopened_by_user_id"),
        CheckConstraint(
            "extract(day FROM period_start) = 1 "
            "AND period_end = (period_start + interval '1 month')::date - 1",
            name="calendar_month",
        ),
        CheckConstraint(
            "closing_balance_cents = opening_balance_cents + total_in_cents - total_out_cents",
            name="balance_arithmetic",
        ),
        CheckConstraint(
            "total_in_cents >= 0 AND total_out_cents >= 0 AND entries_count >= 0",
            name="totals_non_negative",
        ),
        CheckConstraint(f"entries_hash ~ '{HEX64}'", name="entries_hash_format"),
        CheckConstraint(
            "report_ref IS NULL OR length(report_ref) BETWEEN 1 AND 500", name="report_ref_length"
        ),
        CheckConstraint(
            "(reopened_at IS NULL) = (reopened_by_user_id IS NULL) "
            "AND (reopened_at IS NULL) = (reopen_reason IS NULL)",
            name="reopen_complete",
        ),
        CheckConstraint(
            "reopen_reason IS NULL OR length(btrim(reopen_reason)) BETWEEN 10 AND 500",
            name="reopen_reason_length",
        ),
        Index(
            "uq_monthly_closings_active_period",
            "school_id",
            "period_start",
            unique=True,
            postgresql_where=text("reopened_at IS NULL"),
        ),
        Index("ix_monthly_closings_school_id", "school_id"),
        Index("ix_monthly_closings_organization_id", "organization_id"),
    )

    id: Mapped[uuid.UUID] = fin_pk()
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date, server_default=FetchedValue())
    timezone: Mapped[str] = mapped_column(Text, server_default=FetchedValue())
    opening_balance_cents: Mapped[int] = mapped_column(BigInteger, server_default=FetchedValue())
    total_in_cents: Mapped[int] = mapped_column(BigInteger, server_default=FetchedValue())
    total_out_cents: Mapped[int] = mapped_column(BigInteger, server_default=FetchedValue())
    closing_balance_cents: Mapped[int] = mapped_column(BigInteger, server_default=FetchedValue())
    entries_count: Mapped[int] = mapped_column(Integer, server_default=FetchedValue())
    # sha256 (hex) of the canonical text of the entries (see docs/financial-model.md).
    entries_hash: Mapped[str] = mapped_column(Text, server_default=FetchedValue())
    closed_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    report_ref: Mapped[str | None] = mapped_column(Text)
    reopened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_onupdate=FetchedValue()
    )
    reopened_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reopen_reason: Mapped[str | None] = mapped_column(Text)
