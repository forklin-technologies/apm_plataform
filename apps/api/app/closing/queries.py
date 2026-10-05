"""The statements of the closing routes (constants given to `text()`, values bound)."""

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def insert_closing(
    db: Session,
    *,
    organization_id: UUID,
    school_id: UUID,
    period_start: date,
    user_id: UUID,
    bank_balance_reported_cents: int | None,
) -> UUID:
    """The whole of what the application says about a closing: the trigger computes the rest."""
    new_id: UUID = db.execute(
        text(
            "INSERT INTO monthly_closings (organization_id, school_id, period_start, "
            "closed_by_user_id, bank_balance_reported_cents) "
            "VALUES (:organization_id, :school_id, :period_start, :user_id, :bank) RETURNING id"
        ),
        {
            "organization_id": organization_id,
            "school_id": school_id,
            "period_start": period_start,
            "user_id": user_id,
            "bank": bank_balance_reported_cents,
        },
    ).scalar_one()
    return new_id


def get_closing(db: Session, school_id: UUID, closing_id: UUID) -> dict[str, Any] | None:
    row = (
        db.execute(
            text(
                "SELECT c.id, c.school_id, c.period_start, c.period_end, c.timezone, "
                "c.opening_balance_cents, c.contributions_in_cents, c.other_in_cents, "
                "c.refunds_in_cents, c.total_in_cents, c.expenses_out_cents, "
                "c.reimbursements_out_cents, c.total_out_cents, c.closing_balance_cents, "
                "c.pending_reimbursements_cents, c.closing_after_pending_cents, c.entries_count, "
                "c.entries_hash, c.bank_balance_reported_cents, c.bank_difference_cents, "
                "c.closed_by_user_id, c.closed_at, c.report_ref, c.reopened_at, "
                "c.reopened_by_user_id, c.reopen_reason, c.breakdown "
                "FROM monthly_closings c WHERE c.id = :closing_id AND c.school_id = :school_id"
            ),
            {"closing_id": closing_id, "school_id": school_id},
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else dict(row)


def list_closings(
    db: Session,
    school_id: UUID,
    *,
    include_reopened: bool,
    after: dict[str, Any] | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Newest first: (closed_at, id) descending, `limit` rows after the position `after`."""
    rows = db.execute(
        text(
            "SELECT c.id, c.school_id, c.period_start, c.period_end, c.timezone, "
            "c.opening_balance_cents, c.contributions_in_cents, c.other_in_cents, "
            "c.refunds_in_cents, c.total_in_cents, c.expenses_out_cents, "
            "c.reimbursements_out_cents, c.total_out_cents, c.closing_balance_cents, "
            "c.pending_reimbursements_cents, c.closing_after_pending_cents, c.entries_count, "
            "c.entries_hash, c.bank_balance_reported_cents, c.bank_difference_cents, "
            "c.closed_by_user_id, c.closed_at, c.report_ref, c.reopened_at, "
            "c.reopened_by_user_id, c.reopen_reason "
            "FROM monthly_closings c WHERE c.school_id = :school_id "
            "AND (CAST(:include_reopened AS boolean) OR c.reopened_at IS NULL) "
            "AND (CAST(:after_at AS timestamptz) IS NULL OR (c.closed_at, c.id) < "
            "(CAST(:after_at AS timestamptz), CAST(:after_id AS uuid))) "
            "ORDER BY c.closed_at DESC, c.id DESC LIMIT :limit"
        ),
        {
            "school_id": school_id,
            "include_reopened": include_reopened,
            "after_at": None if after is None else after["closed_at"],
            "after_id": None if after is None else after["id"],
            "limit": limit,
        },
    ).mappings()
    return [dict(row) for row in rows]


def verify_closing(db: Session, closing_id: UUID) -> bool:
    return bool(
        db.execute(
            text("SELECT verify_closing(:closing_id)"), {"closing_id": closing_id}
        ).scalar_one()
    )


def reopen_closing(
    db: Session, school_id: UUID, closing_id: UUID, user_id: UUID, reason: str
) -> bool:
    """Reopen an ACTIVE closing; False when no active closing matched (the trigger sets the time and
    refuses everything but the latest active closing)."""
    row = db.execute(
        text(
            "UPDATE monthly_closings SET reopened_by_user_id = :user_id, reopen_reason = :reason "
            "WHERE id = :closing_id AND school_id = :school_id AND reopened_at IS NULL RETURNING id"
        ),
        {"closing_id": closing_id, "school_id": school_id, "user_id": user_id, "reason": reason},
    ).one_or_none()
    return row is not None


def next_period_to_close(db: Session, school_id: UUID) -> date | None:
    """The day after the latest ACTIVE closing, or None when the school has none (any month)."""
    found = db.execute(
        text(
            "SELECT max(c.period_end) + 1 FROM monthly_closings c "
            "WHERE c.school_id = :school_id AND c.reopened_at IS NULL"
        ),
        {"school_id": school_id},
    ).scalar_one_or_none()
    return found if isinstance(found, date) else None
