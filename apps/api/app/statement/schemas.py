"""Response bodies of the statement routes: the COLUMNS of the database functions, unchanged.

States and types keep the vocabulary of the database (PAID, REIMBURSED, INCOME...). Money is an
integer number of cents and every name that holds it ends in `_cents`.
"""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class StatementEntryOut(BaseModel):
    """One cash entry of the period: a column of `statement_entries` each."""

    transaction_id: UUID
    reference_code: int = Field(description="The number the school sees (format: APM-000042).")
    kind: str = Field(description="CONTRIBUTION, EXPENSE, REIMBURSEMENT or REFUND.")
    display_type: str = Field(description="INCOME, EXPENSE or REFUND.")
    direction: str = Field(description="IN or OUT.")
    settled_at: datetime = Field(description="When it was booked in the cash ledger.")
    occurred_at: datetime = Field(description="The real date of the fact.")
    local_date: date = Field(description="The day of `settled_at` in the time zone of the school.")
    amount_cents: int
    signed_amount_cents: int = Field(description="IN positive, OUT negative.")
    late_adjustment: bool
    status: str
    status_label: str = Field(description="Like `status`, but REIMBURSED for a PAID reimbursement.")
    origin_type: str
    origin_user_id: UUID | None
    origin_label: str | None = Field(description="Who the money came from or who spent it.")
    category_id: UUID
    category_key: str
    report_group: str
    description: str | None
    beneficiary_user_id: UUID | None
    beneficiary_label: str | None
    opening_balance_cents: int = Field(
        description="The balance before the period, over ALL the entries (filters leave it as is)."
    )
    running_balance_cents: int = Field(
        description="The balance after this entry, over ALL the entries (filters do not change it)."
    )


class StatementPage(BaseModel):
    items: list[StatementEntryOut]
    next_cursor: str | None = Field(description="Send it back as `cursor` for the next page.")


class SummaryOut(BaseModel):
    """The columns of `statement_summary` for one month, with the two balances.

    `closing_balance_cents` is the PRIMARY balance: what is in cash. `balance_after_pending_cents`
    is the SECONDARY one: the cash minus the reimbursements still waiting to be paid. The pending
    figure is the status NOW, not at the end of the month.
    """

    period: str = Field(examples=["2026-09"])
    period_start: date
    period_end: date
    timezone: str
    opening_balance_cents: int
    contributions_in_cents: int
    other_in_cents: int
    refunds_in_cents: int
    total_in_cents: int
    expenses_out_cents: int = Field(description="Paid by the APM, bank fees included.")
    reimbursements_out_cents: int
    total_out_cents: int
    closing_balance_cents: int = Field(description="PRIMARY balance: in cash.")
    pending_reimbursements_cents: int
    balance_after_pending_cents: int = Field(
        description="SECONDARY balance: cash minus the reimbursements still to be paid."
    )
    entries_count: int


class PendingEntryOut(BaseModel):
    """One row of `statement_pending`: not settled, so outside the balance."""

    transaction_id: UUID
    reference_code: int
    kind: str
    direction: str
    status: str
    amount_cents: int
    section: str = Field(
        description="AWAITING_APPROVAL, AWAITING_CORRECTION, REVIEW, RECEIVABLE or PAYABLE."
    )
    occurred_at: datetime


class PendingPage(BaseModel):
    items: list[PendingEntryOut]
    next_cursor: str | None
