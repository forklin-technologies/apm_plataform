"""When a Pix charge and a contribution that nobody paid stop waiting (ADR-019).

Two rules, both final once applied (EXPIRED never changes again):

- a charge is `PENDING` until `expires_at` plus a grace; only then it becomes `EXPIRED`. A payment
  confirmed right at the edge (clock skew with the provider) must still find a `PENDING` charge;
- a contribution is `PENDING_PAYMENT` until it is `CONTRIBUTION_TTL_HOURS` old and has no `PENDING`
  charge; only then it becomes `EXPIRED`. Before that the family may open a new charge.

The same functions serve the public route (a family polling the page expires what it reads, lazily)
and the job (`app/jobs/expire.py`), so both apply one rule. Everything here runs as `apm_app`,
inside one school, under row level security. The SQL function of migration 0009 repeats the two
numbers to know where to look; `tests/public/test_expiry.py` keeps both in agreement.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import CursorResult, text
from sqlalchemy.orm import Session

EXPIRY_GRACE_SECONDS = 120
CONTRIBUTION_TTL_HOURS = 24
BATCH = 500


@dataclass(frozen=True)
class SchoolReport:
    charges: int = 0
    contributions: int = 0
    more: bool = False  # a full batch was found: run again for the rest


def expire_charges(db: Session, *, transaction_id: uuid.UUID | None = None) -> int:
    """Mark the charges whose expiry plus the grace has passed (of one contribution, or of the
    whole school under row level security). Returns how many."""
    result: CursorResult[Any] = db.execute(  # type: ignore[assignment]
        text(
            "UPDATE pix_charges SET status = 'EXPIRED', updated_at = now() "
            "WHERE status = 'PENDING' AND expires_at + make_interval(secs => :grace) <= now() "
            "AND (CAST(:tx AS uuid) IS NULL OR transaction_id = CAST(:tx AS uuid))"
        ),
        {
            "grace": EXPIRY_GRACE_SECONDS,
            "tx": None if transaction_id is None else str(transaction_id),
        },
    )
    return result.rowcount or 0


_STALE_PENDING_CONTRIBUTION = text(
    "SELECT 1 FROM financial_transactions WHERE id = :tx AND kind = 'CONTRIBUTION' "
    "AND status = 'PENDING_PAYMENT' AND created_at + make_interval(hours => :ttl) <= now()"
)
_LOCK_CONTRIBUTION = text("SELECT id FROM financial_transactions WHERE id = :tx FOR UPDATE")
_EXPIRE_CONTRIBUTION = text(
    "UPDATE financial_transactions SET status = 'EXPIRED', updated_at = now() "
    "WHERE id = :tx AND kind = 'CONTRIBUTION' AND status = 'PENDING_PAYMENT' "
    "AND created_at + make_interval(hours => :ttl) <= now() "
    "AND NOT EXISTS (SELECT 1 FROM pix_charges c "
    "WHERE c.transaction_id = financial_transactions.id AND c.status = 'PENDING')"
)
_CANDIDATES = text(
    "SELECT id FROM financial_transactions WHERE kind = 'CONTRIBUTION' "
    "AND status = 'PENDING_PAYMENT' AND created_at + make_interval(hours => :ttl) <= now() "
    "AND NOT EXISTS (SELECT 1 FROM pix_charges c "
    "WHERE c.transaction_id = financial_transactions.id AND c.status = 'PENDING') "
    "ORDER BY created_at, id LIMIT :batch"
)


def expire_contribution(db: Session, transaction_id: uuid.UUID) -> bool:
    """Expire ONE contribution if it is old enough and no charge is waiting for a payment.

    The contribution is locked first and the conditions are read again in a new statement: a
    family renewing the charge holds that lock while it inserts the new `PENDING` charge, and a
    single UPDATE ... WHERE NOT EXISTS would not see a charge committed while it waited for the
    lock (the re-check of READ COMMITTED keeps the snapshot of the other tables).
    """
    params = {"tx": transaction_id, "ttl": CONTRIBUTION_TTL_HOURS}
    if db.execute(_STALE_PENDING_CONTRIBUTION, params).first() is None:
        return False  # the common case (a fresh contribution): no lock taken
    db.execute(_LOCK_CONTRIBUTION, {"tx": transaction_id})
    updated: CursorResult[Any] = db.execute(_EXPIRE_CONTRIBUTION, params)  # type: ignore[assignment]
    return bool(updated.rowcount)


def expire_in_school(db: Session, *, batch: int = BATCH) -> SchoolReport:
    """Everything that is due in the school the session is bound to (the caller commits).

    Only contributions with no `PENDING` charge are candidates, so a full batch always holds
    expirable ones and `more` says whether another pass is worth it.
    """
    charges = expire_charges(db)
    candidates: list[uuid.UUID] = list(
        db.execute(_CANDIDATES, {"ttl": CONTRIBUTION_TTL_HOURS, "batch": batch}).scalars()
    )
    contributions = sum(int(expire_contribution(db, tx)) for tx in candidates)
    return SchoolReport(charges, contributions, more=len(candidates) == batch)
