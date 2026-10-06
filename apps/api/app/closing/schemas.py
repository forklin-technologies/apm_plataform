"""Bodies of the closing routes. The response repeats the columns of `monthly_closings`."""

from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.statement.periods import PERIOD_PATTERN

# The database accepts a reported bank balance within +-10^12 cents (a CHECK of the table).
BANK_BALANCE_LIMIT = 10**12


class CloseRequest(BaseModel):
    """Close a month. Everything else (the figures, the hash) is computed by the database."""

    model_config = ConfigDict(extra="forbid")

    period: str = Field(pattern=PERIOD_PATTERN, examples=["2026-09"])
    bank_balance_reported_cents: int | None = Field(
        default=None,
        ge=-BANK_BALANCE_LIMIT,
        le=BANK_BALANCE_LIMIT,
        description="The final balance according to the BANK. Given once, with the closing; the "
        "difference to the cash balance is computed and does not stop the closing.",
    )


def _reason_is_long_enough(value: str) -> str:
    stripped = value.strip()
    if not 10 <= len(stripped) <= 500:
        raise ValueError("reason must have 10 to 500 characters")
    return stripped


class ReopenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Annotated[str, Field(max_length=2000), AfterValidator(_reason_is_long_enough)]


class ClosingOut(BaseModel):
    """A closing: the columns of `monthly_closings` (the breakdown is in the detail)."""

    id: UUID
    school_id: UUID
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
    pending_reimbursements_cents: int = Field(description="As of the moment of the closing.")
    closing_after_pending_cents: int = Field(
        description="SECONDARY balance: cash minus the pending reimbursements, as of the closing."
    )
    entries_count: int
    entries_hash: str = Field(description="SHA-256 of the entries: the verification code.")
    bank_balance_reported_cents: int | None
    bank_difference_cents: int | None = Field(
        description="Bank balance minus cash balance; positive when the bank holds more."
    )
    closed_by_user_id: UUID | None = Field(
        description="Hidden from roles that read aggregates only."
    )
    closed_at: datetime
    report_ref: str | None = Field(description="Set once, when the PDF is first generated.")
    reopened_at: datetime | None
    reopened_by_user_id: UUID | None
    reopen_reason: str | None


class ClosingDetailOut(ClosingOut):
    breakdown: dict[str, Any] = Field(
        description="{report_group: {in, out, count, categories: {category_key: {in, out, count}}}}"
    )


class ClosingPage(BaseModel):
    items: list[ClosingOut]
    next_cursor: str | None


class VerifyOut(BaseModel):
    closing_id: UUID
    verified: bool = Field(
        description="True when the figures, the hash and the breakdown recomputed from the ledger "
        "equal the stored snapshot."
    )
    entries_hash: str
