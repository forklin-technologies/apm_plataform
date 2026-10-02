"""The ledger (ADR-015): one base table and one detail table per kind.

The constraints here mirror migration 0007 and carry the same names; triggers, policies and
grants live only in the migration (docs/financial-model.md lists them). Money is bigint cents.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    FetchedValue,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.financial_common import (
    HEX64,
    KINDS,
    ScopeMixin,
    detail_fk,
    fin_pk,
    in_list,
    scope_constraints,
    user_fk,
)
from app.models.mixins import TimestampMixin

DIRECTIONS = ("IN", "OUT")
CONTRIBUTION_STATUSES = ("PENDING_PAYMENT", "PAID", "EXPIRED", "CANCELLED")
EXPENSE_STATUSES = ("SUBMITTED", "APPROVED", "REJECTED", "PAID", "CANCELLED")
REIMBURSEMENT_STATUSES = ("PENDING", "PAID", "CANCELLED")
REFUND_STATUSES = ("PENDING", "PAID", "FAILED")
STATUSES_BY_KIND = {
    "CONTRIBUTION": CONTRIBUTION_STATUSES,
    "EXPENSE": EXPENSE_STATUSES,
    "REIMBURSEMENT": REIMBURSEMENT_STATUSES,
    "REFUND": REFUND_STATUSES,
}
FINAL_STATUSES = ("PAID", "EXPIRED", "CANCELLED", "REJECTED", "FAILED")


class FinancialTransaction(ScopeMixin, TimestampMixin, Base):
    """A movement of money (or the promise of one) of a school. Never edited once final."""

    __tablename__ = "financial_transactions"
    __table_args__ = (
        *scope_constraints("financial_transactions"),
        user_fk("financial_transactions", "created_by_user_id"),
        # Target of the detail tables and of the parent link: same school, declared kind.
        UniqueConstraint(
            "id",
            "organization_id",
            "school_id",
            "kind",
            name="uq_financial_transactions_id_organization_id_school_id_kind",
        ),
        UniqueConstraint(
            "school_id", "reference_code", name="uq_financial_transactions_school_id_reference_code"
        ),
        ForeignKeyConstraint(
            ["parent_transaction_id", "organization_id", "school_id", "parent_kind"],
            [
                "financial_transactions.id",
                "financial_transactions.organization_id",
                "financial_transactions.school_id",
                "financial_transactions.kind",
            ],
            name="fk_financial_transactions_parent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["category_id", "direction", "school_id", "organization_id"],
            [
                "categories.id",
                "categories.applies_to",
                "categories.school_id",
                "categories.organization_id",
            ],
            name="fk_financial_transactions_category",
            ondelete="RESTRICT",
        ),
        CheckConstraint(in_list("kind", KINDS), name="kind_valid"),
        CheckConstraint(in_list("direction", DIRECTIONS), name="direction_valid"),
        CheckConstraint("amount_cents > 0 AND amount_cents <= 1000000000000", name="amount_range"),
        CheckConstraint(
            " OR ".join(
                f"(kind = '{kind}' AND {in_list('status', statuses)})"
                for kind, statuses in STATUSES_BY_KIND.items()
            ),
            name="status_for_kind",
        ),
        CheckConstraint(
            "(kind = 'CONTRIBUTION' AND direction = 'IN' "
            "AND parent_transaction_id IS NULL AND parent_kind IS NULL) "
            "OR (kind = 'EXPENSE' AND direction = 'OUT' "
            "AND parent_transaction_id IS NULL AND parent_kind IS NULL) "
            "OR (kind = 'REIMBURSEMENT' AND direction = 'OUT' "
            "AND parent_transaction_id IS NOT NULL AND parent_kind = 'EXPENSE') "
            "OR (kind = 'REFUND' AND parent_transaction_id IS NOT NULL "
            "AND ((parent_kind = 'CONTRIBUTION' AND direction = 'OUT') "
            "OR (parent_kind = 'EXPENSE' AND direction = 'IN')))",
            name="shape",
        ),
        CheckConstraint(
            "parent_transaction_id IS NULL OR parent_transaction_id <> id", name="not_own_parent"
        ),
        CheckConstraint("(status = 'PAID') = (settled_at IS NOT NULL)", name="paid_iff_settled"),
        CheckConstraint("NOT late_adjustment OR settled_at IS NOT NULL", name="late_needs_settled"),
        CheckConstraint(
            "kind = 'CONTRIBUTION' OR created_by_user_id IS NOT NULL", name="author_required"
        ),
        CheckConstraint(
            "kind <> 'EXPENSE' OR category_id IS NOT NULL", name="expense_has_category"
        ),
        CheckConstraint("reference_code > 0", name="reference_positive"),
        Index(
            "ix_financial_transactions_school_settled",
            "school_id",
            "settled_at",
            "reference_code",
            postgresql_where=text("settled_at IS NOT NULL"),
        ),
        Index("ix_financial_transactions_school_status", "school_id", "status"),
        Index("ix_financial_transactions_school_occurred", "school_id", "occurred_at"),
        Index(
            "ix_financial_transactions_parent",
            "parent_transaction_id",
            postgresql_where=text("parent_transaction_id IS NOT NULL"),
        ),
        Index(
            "ix_financial_transactions_category",
            "category_id",
            postgresql_where=text("category_id IS NOT NULL"),
        ),
        Index("ix_financial_transactions_organization_id", "organization_id"),
        Index(
            "uq_financial_transactions_one_active_reimbursement",
            "school_id",
            "parent_transaction_id",
            unique=True,
            postgresql_where=text("kind = 'REIMBURSEMENT' AND status IN ('PENDING', 'PAID')"),
        ),
    )

    id: Mapped[uuid.UUID] = fin_pk()
    kind: Mapped[str] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(Text)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text)
    category_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # When it happened (the real date of the fact).
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    # When it was BOOKED in the cash ledger. A settlement dated in a closed month is booked now and
    # flagged late_adjustment by the database (trigger ft_30_settle), so the application reads the
    # stored value back: both attributes are refreshed after an UPDATE.
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_onupdate=FetchedValue()
    )
    late_adjustment: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), server_onupdate=FetchedValue()
    )
    parent_transaction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    parent_kind: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Assigned by the database (gapless, per school): never set by the application.
    reference_code: Mapped[int] = mapped_column(BigInteger, server_default=FetchedValue())


class Contribution(ScopeMixin, TimestampMixin, Base):
    """Detail of a CONTRIBUTION: how it was made and who gave (optional, per school setting)."""

    __tablename__ = "contributions"
    __table_args__ = (
        *scope_constraints("contributions"),
        detail_fk("contributions"),
        UniqueConstraint("receipt_token_hash", name="uq_contributions_receipt_token_hash"),
        CheckConstraint("kind = 'CONTRIBUTION'", name="kind"),
        CheckConstraint("method IN ('PIX', 'CASH')", name="method_valid"),
        CheckConstraint(
            "guardian_name IS NULL OR length(btrim(guardian_name)) BETWEEN 1 AND 120",
            name="guardian_name_length",
        ),
        CheckConstraint(
            "student_name IS NULL OR length(btrim(student_name)) BETWEEN 1 AND 120",
            name="student_name_length",
        ),
        CheckConstraint(
            "class_name IS NULL OR length(btrim(class_name)) BETWEEN 1 AND 120",
            name="class_name_length",
        ),
        CheckConstraint(
            f"receipt_token_hash IS NULL OR receipt_token_hash ~ '{HEX64}'",
            name="receipt_token_hash_format",
        ),
        CheckConstraint(
            "method <> 'PIX' "
            "OR (receipt_token_hash IS NOT NULL AND receipt_expires_at IS NOT NULL)",
            name="pix_has_receipt",
        ),
        Index("ix_contributions_school_id", "school_id"),
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, server_default=text("'CONTRIBUTION'"))
    method: Mapped[str] = mapped_column(Text)
    # Personal data of a child's family: only when the school enables it, never public. They can
    # only be erased (non-null to null), never rewritten (trigger contributions_10_anonymize).
    guardian_name: Mapped[str | None] = mapped_column(Text)
    student_name: Mapped[str | None] = mapped_column(Text)
    class_name: Mapped[str | None] = mapped_column(Text)
    # SHA-256 of the receipt token; the token itself is never stored (ADR-010).
    receipt_token_hash: Mapped[str | None] = mapped_column(Text)
    receipt_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Expense(ScopeMixin, TimestampMixin, Base):
    __tablename__ = "expenses"
    __table_args__ = (
        *scope_constraints("expenses"),
        detail_fk("expenses"),
        user_fk("expenses", "submitted_by_user_id"),
        user_fk("expenses", "approved_by_user_id"),
        CheckConstraint("kind = 'EXPENSE'", name="kind"),
        CheckConstraint("length(btrim(description)) BETWEEN 1 AND 500", name="description_length"),
        CheckConstraint(
            "vendor IS NULL OR length(btrim(vendor)) BETWEEN 1 AND 200", name="vendor_length"
        ),
        CheckConstraint("paid_by IN ('APM', 'COLLABORATOR')", name="paid_by_valid"),
        # Separation of duties, in the database: nobody decides their own expense.
        CheckConstraint(
            "approved_by_user_id IS NULL OR approved_by_user_id <> submitted_by_user_id",
            name="decider_is_not_submitter",
        ),
        CheckConstraint(
            "(approved_by_user_id IS NULL) = (approved_at IS NULL)", name="decision_complete"
        ),
        CheckConstraint(
            "decision_reason IS NULL OR length(btrim(decision_reason)) BETWEEN 1 AND 500",
            name="decision_reason_length",
        ),
        Index("ix_expenses_school_id", "school_id"),
        Index("ix_expenses_submitted_by_user_id", "submitted_by_user_id"),
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, server_default=text("'EXPENSE'"))
    description: Mapped[str] = mapped_column(Text)
    vendor: Mapped[str | None] = mapped_column(Text)
    paid_by: Mapped[str] = mapped_column(Text)
    submitted_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    # Who decided (approved or rejected). approved_at is set by the database (trigger).
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_onupdate=FetchedValue()
    )
    decision_reason: Mapped[str | None] = mapped_column(Text)


class Reimbursement(ScopeMixin, TimestampMixin, Base):
    """Money the APM pays back to a collaborator who paid an expense. Paid outside the system."""

    __tablename__ = "reimbursements"
    __table_args__ = (
        *scope_constraints("reimbursements"),
        detail_fk("reimbursements"),
        user_fk("reimbursements", "beneficiary_user_id"),
        CheckConstraint("kind = 'REIMBURSEMENT'", name="kind"),
        CheckConstraint(
            "payment_reference IS NULL OR length(btrim(payment_reference)) BETWEEN 1 AND 200",
            name="payment_reference_length",
        ),
        Index("ix_reimbursements_school_id", "school_id"),
        Index("ix_reimbursements_beneficiary_user_id", "beneficiary_user_id"),
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, server_default=text("'REIMBURSEMENT'"))
    beneficiary_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    payment_reference: Mapped[str | None] = mapped_column(Text)


class Refund(ScopeMixin, TimestampMixin, Base):
    """The return of a contribution (money out) or of an expense paid by the APM (money in)."""

    __tablename__ = "refunds"
    __table_args__ = (
        *scope_constraints("refunds"),
        detail_fk("refunds"),
        CheckConstraint("kind = 'REFUND'", name="kind"),
        CheckConstraint("length(btrim(reason)) BETWEEN 3 AND 500", name="reason_length"),
        CheckConstraint(
            "payment_reference IS NULL OR length(btrim(payment_reference)) BETWEEN 1 AND 200",
            name="payment_reference_length",
        ),
        Index("ix_refunds_school_id", "school_id"),
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, server_default=text("'REFUND'"))
    reason: Mapped[str] = mapped_column(Text)
    payment_reference: Mapped[str | None] = mapped_column(Text)


class ExpenseAttachment(ScopeMixin, TimestampMixin, Base):
    """A receipt attached to an expense. Insert-only; the bytes live behind a storage key."""

    __tablename__ = "expense_attachments"
    __table_args__ = (
        *scope_constraints("expense_attachments"),
        ForeignKeyConstraint(
            ["transaction_id", "organization_id", "school_id"],
            ["expenses.transaction_id", "expenses.organization_id", "expenses.school_id"],
            name="fk_expense_attachments_transaction_id_expenses",
            ondelete="RESTRICT",
        ),
        user_fk("expense_attachments", "uploaded_by_user_id"),
        UniqueConstraint(
            "school_id",
            "transaction_id",
            "sha256",
            name="uq_expense_attachments_school_id_transaction_id_sha256",
        ),
        CheckConstraint(
            "length(storage_key) BETWEEN 1 AND 500 AND storage_key !~ '^/'",
            name="storage_key_length",
        ),
        CheckConstraint("length(btrim(file_name)) BETWEEN 1 AND 255", name="file_name_length"),
        CheckConstraint("content_type ~ '^[a-z0-9.+-]+/[a-z0-9.+-]+$'", name="content_type_format"),
        CheckConstraint("size_bytes BETWEEN 1 AND 20971520", name="size_range"),
        CheckConstraint(f"sha256 ~ '{HEX64}'", name="sha256_format"),
        Index("ix_expense_attachments_transaction_id", "transaction_id"),
        Index("ix_expense_attachments_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = fin_pk()
    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    storage_key: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(Text)
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
