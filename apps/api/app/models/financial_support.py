"""Support tables of the financial schema: categories, settings, accounts, Pix, webhooks, audit."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
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
REPORT_GROUPS = ("CONTRIBUTIONS", "OTHER_INCOME", "EXPENSES_REIMBURSEMENTS", "REFUNDS")
IDENTIFICATION_FIELDS = (
    "ARRAY['guardian_name', 'contributor_email', 'contributor_phone', 'student_name', "
    "'class_name']::text[]"
)


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
        CheckConstraint(
            "(applies_to = 'IN' AND report_group IN ('CONTRIBUTIONS', 'OTHER_INCOME', 'REFUNDS')) "
            "OR (applies_to = 'OUT' AND report_group IN ('EXPENSES_REIMBURSEMENTS', 'BANK_FEES'))",
            name="group_matches_direction",
        ),
        # Only the bank fees may go without an approver.
        CheckConstraint(
            "requires_approval OR report_group = 'BANK_FEES'", name="no_approval_only_for_bank_fees"
        ),
        Index("ix_categories_school_id", "school_id"),
        Index("ix_categories_organization_id", "organization_id"),
    )

    id: Mapped[uuid.UUID] = fin_pk()
    key: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    applies_to: Mapped[str] = mapped_column(Text)
    # Where the category goes in the monthly report.
    report_group: Mapped[str] = mapped_column(Text)
    # false: an expense in this category needs no approver (bank fees): it is born APPROVED and
    # settled by whoever records it, audited like any other. Written once, with the category.
    requires_approval: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
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
        CheckConstraint(
            "cardinality(suggested_amounts_cents) BETWEEN 1 AND 6 "
            "AND array_position(suggested_amounts_cents, NULL) IS NULL "
            "AND min_contribution_cents <= ALL (suggested_amounts_cents) "
            "AND max_contribution_cents >= ALL (suggested_amounts_cents)",
            name="suggested_amounts_valid",
        ),
        CheckConstraint("pix_expiration_minutes BETWEEN 5 AND 1440", name="pix_expiration_range"),
        CheckConstraint(
            f"required_fields <@ {IDENTIFICATION_FIELDS} "
            f"AND optional_fields <@ {IDENTIFICATION_FIELDS} "
            "AND NOT (required_fields && optional_fields)",
            name="identification_fields_valid",
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
    suggested_amounts_cents: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), server_default=text("'{2000,3000,4000}'")
    )
    allow_custom_amount: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    pix_expiration_minutes: Mapped[int] = mapped_column(Integer, server_default=text("30"))
    # Which identification fields the public form asks: two lists (anonymous = both empty).
    required_fields: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    optional_fields: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        server_default=text("'{guardian_name,contributor_email,contributor_phone}'"),
    )
    brand_accent: Mapped[str] = mapped_column(Text, server_default=text("'#0a84ff'"))
    brand_accent_contrast: Mapped[str] = mapped_column(Text, server_default=text("'#ffffff'"))
    # NULL = off. An expense above it needs the organization administrator (rule in the service).
    approval_limit_cents: Mapped[int | None] = mapped_column(BigInteger)


class PaymentAccount(ScopeMixin, TimestampMixin, Base):
    """The receiving account of a school (one ACTIVE). It holds NO credential: secret_ref is only a
    reference to a secrets manager, and webhook_secret_hash is the SHA-256 of the 128-bit secret of
    the webhook (a hash, not a credential) that the application role cannot read."""

    __tablename__ = "payment_accounts"
    __table_args__ = (
        *scope_constraints("payment_accounts"),
        # Target of pix_charges (payment_account_id, organization_id, school_id).
        UniqueConstraint(
            "id",
            "organization_id",
            "school_id",
            name="uq_payment_accounts_id_organization_id_school_id",
        ),
        UniqueConstraint(
            "school_id",
            "provider",
            "external_account_id",
            name="uq_payment_accounts_school_id_provider_external_account_id",
        ),
        # Global for the lookup without a tenant; the hash of a 128-bit secret reveals nothing.
        UniqueConstraint("webhook_secret_hash", name="uq_payment_accounts_webhook_secret_hash"),
        CheckConstraint("provider IN ('BB', 'SANDBOX')", name="provider_valid"),
        CheckConstraint(
            "length(btrim(external_account_id)) BETWEEN 1 AND 100",
            name="external_account_id_length",
        ),
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE', 'PENDING')", name="status_valid"),
        CheckConstraint(
            "secret_ref ~ '^[a-z][a-z0-9_]{1,19}:[A-Za-z0-9_./-]{1,200}$'", name="secret_ref_format"
        ),
        CheckConstraint(
            f"webhook_secret_hash IS NULL OR webhook_secret_hash ~ '{HEX64}'",
            name="webhook_secret_hash_format",
        ),
        Index(
            "uq_payment_accounts_one_active_per_school",
            "school_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_payment_accounts_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = fin_pk()
    provider: Mapped[str] = mapped_column(Text)
    external_account_id: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'PENDING'"))
    # "scheme:path" (env:NAME, file:path, vault:path): a reference, never the secret itself.
    secret_ref: Mapped[str] = mapped_column(Text)
    # The application role has no SELECT on this column (like users.password_hash): deferred, so
    # session.get(PaymentAccount, id) works.
    webhook_secret_hash: Mapped[str | None] = mapped_column(Text, deferred=True)


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
        ForeignKeyConstraint(
            ["payment_account_id", "organization_id", "school_id"],
            [
                "payment_accounts.id",
                "payment_accounts.organization_id",
                "payment_accounts.school_id",
            ],
            name="fk_pix_charges_payment_account_id_payment_accounts",
            ondelete="RESTRICT",
        ),
        # Per school, never global (a global UNIQUE would be an oracle across tenants).
        UniqueConstraint(
            "school_id", "provider", "txid", name="uq_pix_charges_school_id_provider_txid"
        ),
        CheckConstraint("provider IN ('BB', 'SANDBOX')", name="provider_valid"),
        CheckConstraint("txid ~ '^[A-Za-z0-9]{26,35}$'", name="txid_format"),
        CheckConstraint(
            "status IN ('PENDING', 'PAID', 'REVIEW_REQUIRED', 'EXPIRED', 'CANCELLED')",
            name="status_valid",
        ),
        CheckConstraint("amount_cents > 0 AND amount_cents <= 1000000000000", name="amount_range"),
        CheckConstraint(
            "received_amount_cents IS NULL "
            "OR (received_amount_cents > 0 AND received_amount_cents <= 1000000000000)",
            name="received_amount_range",
        ),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        CheckConstraint(
            "end_to_end_id IS NULL OR end_to_end_id ~ '^E[A-Za-z0-9]{31}$'",
            name="end_to_end_id_format",
        ),
        CheckConstraint(
            "(status IN ('PAID', 'REVIEW_REQUIRED')) = "
            "(end_to_end_id IS NOT NULL AND paid_at IS NOT NULL "
            "AND received_amount_cents IS NOT NULL)",
            name="paid_iff_confirmed",
        ),
        CheckConstraint(
            "(status <> 'PAID' OR received_amount_cents = amount_cents) "
            "AND (status <> 'REVIEW_REQUIRED' OR (received_amount_cents <> amount_cents "
            "AND length(btrim(coalesce(divergence_reason, ''))) >= 3))",
            name="received_matches_status",
        ),
        CheckConstraint(
            "divergence_reason IS NULL OR length(btrim(divergence_reason)) BETWEEN 3 AND 500",
            name="divergence_reason_length",
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
        Index("ix_pix_charges_payment_account_id", "payment_account_id"),
        Index("ix_pix_charges_school_id", "school_id"),
        Index(
            "ix_pix_charges_pending_expiry",
            "expires_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    id: Mapped[uuid.UUID] = fin_pk()
    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    payment_account_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    provider: Mapped[str] = mapped_column(Text)
    txid: Mapped[str] = mapped_column(Text)
    # PENDING until the provider answers: PAID (exactly the amount), REVIEW_REQUIRED (a different
    # amount: the management decides), EXPIRED or CANCELLED.
    status: Mapped[str] = mapped_column(Text, server_default=text("'PENDING'"))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    received_amount_cents: Mapped[int | None] = mapped_column(BigInteger)
    divergence_reason: Mapped[str | None] = mapped_column(Text)
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
            "entity_reference IS NULL OR entity_reference > 0", name="entity_reference_positive"
        ),
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
        Index(
            "ix_audit_logs_school_reference",
            "school_id",
            "entity_reference",
            postgresql_where=text("entity_reference IS NOT NULL"),
        ),
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
    # The reference_code of the movement, for readable sentences ("... APM-20261002-000042").
    entity_reference: Mapped[int | None] = mapped_column(BigInteger)
    # Never personal data: ids and non-personal columns only.
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(Text, server_default=FetchedValue())
    ip: Mapped[str | None] = mapped_column(INET, server_default=FetchedValue())
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MonthlyClosing(ScopeMixin, Base):
    """A closed month. Every figure, the hash and the breakdown are computed by the database."""

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
            "total_in_cents = contributions_in_cents + other_in_cents + refunds_in_cents "
            "AND total_out_cents = expenses_out_cents + reimbursements_out_cents",
            name="totals_add_up",
        ),
        CheckConstraint(
            "closing_balance_cents = opening_balance_cents + total_in_cents - total_out_cents",
            name="balance_arithmetic",
        ),
        CheckConstraint(
            "closing_after_pending_cents = closing_balance_cents - pending_reimbursements_cents",
            name="committed_arithmetic",
        ),
        CheckConstraint(
            "contributions_in_cents >= 0 AND other_in_cents >= 0 AND refunds_in_cents >= 0 "
            "AND expenses_out_cents >= 0 AND reimbursements_out_cents >= 0 "
            "AND pending_reimbursements_cents >= 0 AND entries_count >= 0",
            name="totals_non_negative",
        ),
        CheckConstraint(f"entries_hash ~ '{HEX64}'", name="entries_hash_format"),
        CheckConstraint("jsonb_typeof(breakdown) = 'object'", name="breakdown_object"),
        CheckConstraint(
            "bank_balance_reported_cents IS NULL "
            "OR bank_balance_reported_cents BETWEEN -1000000000000 AND 1000000000000",
            name="bank_balance_range",
        ),
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
    # The cash figures, computed by statement_summary at the moment of the closing.
    opening_balance_cents: Mapped[int] = mapped_column(BigInteger, server_default=FetchedValue())
    contributions_in_cents: Mapped[int] = mapped_column(BigInteger, server_default=FetchedValue())
    other_in_cents: Mapped[int] = mapped_column(BigInteger, server_default=FetchedValue())
    refunds_in_cents: Mapped[int] = mapped_column(BigInteger, server_default=FetchedValue())
    total_in_cents: Mapped[int] = mapped_column(BigInteger, server_default=FetchedValue())
    expenses_out_cents: Mapped[int] = mapped_column(BigInteger, server_default=FetchedValue())
    reimbursements_out_cents: Mapped[int] = mapped_column(BigInteger, server_default=FetchedValue())
    total_out_cents: Mapped[int] = mapped_column(BigInteger, server_default=FetchedValue())
    closing_balance_cents: Mapped[int] = mapped_column(BigInteger, server_default=FetchedValue())
    # A photograph of the moment (the history of a status is not stored): not in the hash.
    pending_reimbursements_cents: Mapped[int] = mapped_column(
        BigInteger, server_default=FetchedValue()
    )
    closing_after_pending_cents: Mapped[int] = mapped_column(
        BigInteger, server_default=FetchedValue()
    )
    entries_count: Mapped[int] = mapped_column(Integer, server_default=FetchedValue())
    # sha256 (hex) of the canonical text of the entries (see docs/financial-model.md).
    entries_hash: Mapped[str] = mapped_column(Text, server_default=FetchedValue())
    # {report_group: {in, out, count, categories: {key: {in, out, count}}}}
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=FetchedValue())
    # Reconciliation: the final balance according to the bank, given when the month is closed (and
    # never changed), and the difference to the cash balance of the platform, computed by the
    # database (positive: the bank holds more). Neither is part of the hash or of verify_closing.
    bank_balance_reported_cents: Mapped[int | None] = mapped_column(BigInteger)
    bank_difference_cents: Mapped[int | None] = mapped_column(
        BigInteger,
        Computed("bank_balance_reported_cents - closing_balance_cents", persisted=True),
    )
    closed_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    report_ref: Mapped[str | None] = mapped_column(Text)
    reopened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_onupdate=FetchedValue()
    )
    reopened_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reopen_reason: Mapped[str | None] = mapped_column(Text)
