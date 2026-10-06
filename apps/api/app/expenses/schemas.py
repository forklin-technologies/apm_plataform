"""The HTTP contract of the expenses module (ADR-017): snake_case, money in cents, ISO 8601 dates,
and the states of the database with no translation."""

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, Field, StringConstraints

from app.schemas.auth import Strict, _storable

ExpenseStatus = Literal[
    "DRAFT", "SUBMITTED", "CORRECTION_REQUESTED", "APPROVED", "REJECTED", "PAID", "CANCELLED"
]
PaymentMethod = Literal["PIX", "CARD", "CASH", "OTHER"]
PaidBy = Literal["APM", "COLLABORATOR"]
AttachmentKind = Literal["INVOICE", "PAYMENT_PROOF", "OTHER"]

# What the database accepts (`ck_financial_transactions_amount_range`).
MAX_AMOUNT_CENTS = 1_000_000_000_000
MIN_REASON_LENGTH = 3

Cents = Annotated[int, Field(strict=True, gt=0, le=MAX_AMOUNT_CENTS)]


# Stripped text of a length, and storable (no NUL byte, no lone surrogate). The constraints come
# BEFORE the check: a constraint written after a validator is not applied to the text.
Description = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    AfterValidator(_storable),
]
Vendor = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    AfterValidator(_storable),
]
PurchaseReason = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=MIN_REASON_LENGTH, max_length=500),
    AfterValidator(_storable),
]
Reason = PurchaseReason
PaymentReference = Vendor

# A full instant (with its UTC offset) or just a day. A day means noon of that day in the time
# zone of the school, so the date a person typed is the date the school sees.
OccurredAt = AwareDatetime | dt.date


class ExpenseCreate(Strict):
    amount_cents: Cents
    occurred_at: OccurredAt
    category_id: uuid.UUID
    description: Description
    vendor: Vendor | None = None
    purchase_reason: PurchaseReason | None = None
    payment_method: PaymentMethod | None = None
    paid_by: PaidBy


class ExpenseUpdate(Strict):
    """Only the fields present are changed. `vendor`, `purchase_reason` and `payment_method` may be
    set to null; the others cannot. `paid_by` cannot change (the database never lets it)."""

    amount_cents: Cents | None = None
    occurred_at: OccurredAt | None = None
    category_id: uuid.UUID | None = None
    description: Description | None = None
    vendor: Vendor | None = None
    purchase_reason: PurchaseReason | None = None
    payment_method: PaymentMethod | None = None


class ApproveRequest(Strict):
    # Omitted = the amount requested. Lower is allowed only for an expense paid by a collaborator.
    approved_amount_cents: Cents | None = None
    reason: Reason | None = None


class ReasonRequest(Strict):
    reason: Reason


class ReimburseRequest(Strict):
    # How the payment, already made in the bank by the treasury, can be found there.
    payment_reference: PaymentReference


class CategoryRef(BaseModel):
    id: uuid.UUID
    key: str
    name: str


class AttachmentOut(BaseModel):
    id: uuid.UUID
    kind: AttachmentKind
    file_name: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_by_user_id: uuid.UUID
    created_at: dt.datetime


class ReimbursementOut(BaseModel):
    id: uuid.UUID
    status: Literal["PENDING", "PAID", "CANCELLED"]
    amount_cents: int
    beneficiary_user_id: uuid.UUID
    paid_by_user_id: uuid.UUID | None
    payment_reference: str | None
    settled_at: dt.datetime | None
    created_at: dt.datetime


class ExpenseSummary(BaseModel):
    id: uuid.UUID
    reference_code: int
    status: ExpenseStatus
    amount_cents: int
    approved_amount_cents: int | None
    category: CategoryRef
    occurred_at: dt.datetime
    description: str
    vendor: str | None
    paid_by: PaidBy
    submitted_by_user_id: uuid.UUID
    attachments_count: int
    created_at: dt.datetime
    updated_at: dt.datetime


class ExpenseDetail(ExpenseSummary):
    purchase_reason: str | None
    payment_method: PaymentMethod | None
    approved_by_user_id: uuid.UUID | None
    approved_at: dt.datetime | None
    decision_reason: str | None
    correction_reason: str | None
    settled_at: dt.datetime | None
    attachments: list[AttachmentOut]
    reimbursement: ReimbursementOut | None


class ExpensePage(BaseModel):
    items: list[ExpenseSummary]
    next_cursor: str | None


class CategoryList(BaseModel):
    items: list[CategoryRef]
