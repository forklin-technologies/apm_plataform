"""Builders for the financial tests: a school with people and categories, and the movements of a
ledger. Everything here runs on a connection of the admin of the dev database (a superuser), so the
rows are written the way a migration or a seed would write them: row level security does not get in
the way and EVERY trigger fires (the guards, the booking, the reference code, the audit).

Most tests use these builders inside a transaction that is rolled back, so nothing has to be
cleaned up. The deferred consistency trigger then never runs on its own; call `check_consistency`
to run it on purpose.
"""

# ruff: noqa: E501  (SQL text)

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Connection, text

TZ_SP = "America/Sao_Paulo"


def utc(*args: int) -> datetime:
    """A UTC instant: utc(2025, 3, 10, 15, 0)."""
    return datetime(*args, tzinfo=UTC)  # type: ignore[arg-type, misc]


def token_hash() -> str:
    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()


def end_to_end_id() -> str:
    return "E" + uuid.uuid4().hex[:31]


@dataclass(frozen=True)
class Fresh:
    """A school of its own, with an organization, three members and one category per direction."""

    org: uuid.UUID
    school: uuid.UUID
    admin: uuid.UUID  # organization_admin: a membership of the whole organization
    treasurer: uuid.UUID  # a membership of the school
    staff: uuid.UUID  # a membership of the school
    outsider: uuid.UUID  # a user with no membership at all
    cat_in: uuid.UUID
    cat_out: uuid.UUID


def make_school(conn: Connection, *, timezone: str | None = None) -> Fresh:
    suffix = uuid.uuid4().hex[:12]
    org, school = uuid.uuid4(), uuid.uuid4()
    conn.execute(
        text("INSERT INTO organizations (id, name, slug) VALUES (:id, 'Org T5', :slug)"),
        {"id": org, "slug": f"t5-org-{suffix}"},
    )
    conn.execute(
        text(
            "INSERT INTO schools (id, organization_id, name, slug) VALUES (:id, :org, 'School T5', :slug)"
        ),
        {"id": school, "org": org, "slug": f"t5-sch-{suffix}"},
    )
    people = {name: uuid.uuid4() for name in ("admin", "treasurer", "staff", "outsider")}
    for name, user in people.items():
        conn.execute(
            text("INSERT INTO users (id, email, full_name) VALUES (:id, :email, :name)"),
            {"id": user, "email": f"t5-{name}-{suffix}@example.test", "name": f"T5 {name}"},
        )
    for name, role, school_id in (
        ("admin", "organization_admin", None),
        ("treasurer", "treasurer", school),
        ("staff", "staff", school),
    ):
        conn.execute(
            text(
                "INSERT INTO memberships (user_id, organization_id, school_id, role, status) "
                "VALUES (:user, :org, :school, :role, 'active')"
            ),
            {"user": people[name], "org": org, "school": school_id, "role": role},
        )
    cat_in, cat_out = uuid.uuid4(), uuid.uuid4()
    for category, key, applies_to in ((cat_in, "t5_in", "IN"), (cat_out, "t5_out", "OUT")):
        conn.execute(
            text(
                "INSERT INTO categories (id, organization_id, school_id, key, name, applies_to) "
                "VALUES (:id, :org, :school, :key, :key, :applies_to)"
            ),
            {"id": category, "org": org, "school": school, "key": key, "applies_to": applies_to},
        )
    if timezone is not None:
        conn.execute(
            text("UPDATE school_settings SET timezone = :tz WHERE school_id = :school"),
            {"tz": timezone, "school": school},
        )
    return Fresh(
        org=org,
        school=school,
        admin=people["admin"],
        treasurer=people["treasurer"],
        staff=people["staff"],
        outsider=people["outsider"],
        cat_in=cat_in,
        cat_out=cat_out,
    )


def drop_school(conn: Connection, fresh: Fresh) -> None:
    """Remove a COMMITTED fresh school and everything under it (the triggers are skipped)."""
    from tests.dbsupport import purge_financial

    purge_financial(conn, [fresh.org])
    conn.execute(text("DELETE FROM memberships WHERE organization_id = :o"), {"o": fresh.org})
    conn.execute(text("DELETE FROM users WHERE email LIKE :p"), {"p": f"t5-%-{fresh.school}%"})
    conn.execute(text("DELETE FROM schools WHERE organization_id = :o"), {"o": fresh.org})
    conn.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": fresh.org})


def add_transaction(
    conn: Connection,
    fresh: Fresh,
    *,
    kind: str,
    direction: str,
    amount: int,
    status: str,
    category: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
    settled_at: datetime | None = None,
    parent: uuid.UUID | None = None,
    parent_kind: str | None = None,
) -> uuid.UUID:
    return conn.execute(
        text(
            "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, "
            "amount_cents, status, category_id, occurred_at, settled_at, parent_transaction_id, "
            "parent_kind, created_by_user_id) "
            "VALUES (:org, :school, :kind, :direction, :amount, :status, :category, "
            "coalesce(:occurred_at, now()), :settled_at, :parent, :parent_kind, :created_by) "
            "RETURNING id"
        ),
        {
            "org": fresh.org,
            "school": fresh.school,
            "kind": kind,
            "direction": direction,
            "amount": amount,
            "status": status,
            "category": category,
            "occurred_at": occurred_at,
            "settled_at": settled_at,
            "parent": parent,
            "parent_kind": parent_kind,
            "created_by": created_by,
        },
    ).scalar_one()


def add_cash_contribution(
    conn: Connection,
    fresh: Fresh,
    amount: int,
    settled_at: datetime,
    *,
    guardian: str | None = None,
    student: str | None = None,
    class_name: str | None = None,
) -> uuid.UUID:
    """A cash contribution is born PAID and records who entered it."""
    tx = add_transaction(
        conn,
        fresh,
        kind="CONTRIBUTION",
        direction="IN",
        amount=amount,
        status="PAID",
        category=fresh.cat_in,
        created_by=fresh.treasurer,
        occurred_at=settled_at,
        settled_at=settled_at,
    )
    conn.execute(
        text(
            "INSERT INTO contributions (transaction_id, organization_id, school_id, method, "
            "guardian_name, student_name, class_name) "
            "VALUES (:tx, :org, :school, 'CASH', :guardian, :student, :class_name)"
        ),
        {
            "tx": tx,
            "org": fresh.org,
            "school": fresh.school,
            "guardian": guardian,
            "student": student,
            "class_name": class_name,
        },
    )
    return tx


def add_pix_contribution(
    conn: Connection, fresh: Fresh, amount: int, *, paid_at: datetime | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    """A public Pix contribution with its charge. With paid_at it ends PAID (charge confirmed
    first, the way only the provider's answer can make it so). Returns (transaction, charge)."""
    tx = add_transaction(
        conn,
        fresh,
        kind="CONTRIBUTION",
        direction="IN",
        amount=amount,
        status="PENDING_PAYMENT",
        category=fresh.cat_in,
    )
    conn.execute(
        text(
            "INSERT INTO contributions (transaction_id, organization_id, school_id, method, "
            "receipt_token_hash, receipt_expires_at) "
            "VALUES (:tx, :org, :school, 'PIX', :hash, now() + interval '30 days')"
        ),
        {"tx": tx, "org": fresh.org, "school": fresh.school, "hash": token_hash()},
    )
    charge: uuid.UUID = conn.execute(
        text(
            "INSERT INTO pix_charges (organization_id, school_id, transaction_id, provider, txid, "
            "amount_cents, expires_at) "
            "VALUES (:org, :school, :tx, 'SANDBOX', :txid, :amount, now() + interval '30 minutes') "
            "RETURNING id"
        ),
        {
            "org": fresh.org,
            "school": fresh.school,
            "tx": tx,
            "txid": uuid.uuid4().hex,
            "amount": amount,
        },
    ).scalar_one()
    if paid_at is not None:
        conn.execute(
            text(
                "UPDATE pix_charges SET status = 'PAID', end_to_end_id = :e2e, paid_at = :paid_at "
                "WHERE id = :id"
            ),
            {"e2e": end_to_end_id(), "paid_at": paid_at, "id": charge},
        )
        conn.execute(
            text(
                "UPDATE financial_transactions SET status = 'PAID', settled_at = :paid_at "
                "WHERE id = :id"
            ),
            {"paid_at": paid_at, "id": tx},
        )
    return tx, charge


def add_expense(
    conn: Connection,
    fresh: Fresh,
    amount: int,
    *,
    paid_by: str = "APM",
    status: str = "SUBMITTED",
    settled_at: datetime | None = None,
    occurred_at: datetime | None = None,
) -> uuid.UUID:
    """An expense submitted by `staff` and decided by `treasurer` (never the same person)."""
    tx = add_transaction(
        conn,
        fresh,
        kind="EXPENSE",
        direction="OUT",
        amount=amount,
        status=status,
        category=fresh.cat_out,
        created_by=fresh.staff,
        occurred_at=occurred_at,
        settled_at=settled_at,
    )
    decided = status in ("APPROVED", "REJECTED", "PAID")
    conn.execute(
        text(
            "INSERT INTO expenses (transaction_id, organization_id, school_id, description, "
            "paid_by, submitted_by_user_id, approved_by_user_id, decision_reason) "
            "VALUES (:tx, :org, :school, 'Material', :paid_by, :staff, :decider, :reason)"
        ),
        {
            "tx": tx,
            "org": fresh.org,
            "school": fresh.school,
            "paid_by": paid_by,
            "staff": fresh.staff,
            "decider": fresh.treasurer if decided else None,
            "reason": "no budget" if status == "REJECTED" else None,
        },
    )
    return tx


def add_reimbursement(
    conn: Connection,
    fresh: Fresh,
    expense: uuid.UUID,
    amount: int,
    *,
    status: str = "PENDING",
    settled_at: datetime | None = None,
    reference: str | None = None,
) -> uuid.UUID:
    tx = add_transaction(
        conn,
        fresh,
        kind="REIMBURSEMENT",
        direction="OUT",
        amount=amount,
        status=status,
        created_by=fresh.treasurer,
        occurred_at=settled_at,
        settled_at=settled_at,
        parent=expense,
        parent_kind="EXPENSE",
    )
    conn.execute(
        text(
            "INSERT INTO reimbursements (transaction_id, organization_id, school_id, "
            "beneficiary_user_id, payment_reference) VALUES (:tx, :org, :school, :staff, :ref)"
        ),
        {
            "tx": tx,
            "org": fresh.org,
            "school": fresh.school,
            "staff": fresh.staff,
            "ref": reference,
        },
    )
    return tx


def add_collaborator_expense(
    conn: Connection, fresh: Fresh, amount: int, *, reimbursed_at: datetime | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    """An expense paid by a collaborator, approved, and its reimbursement (PAID when reimbursed_at
    is given). The expense itself never moves the cash. Returns (expense, reimbursement)."""
    expense = add_expense(conn, fresh, amount, paid_by="COLLABORATOR", status="APPROVED")
    if reimbursed_at is None:
        return expense, add_reimbursement(conn, fresh, expense, amount)
    reimbursement = add_reimbursement(
        conn,
        fresh,
        expense,
        amount,
        status="PAID",
        settled_at=reimbursed_at,
        reference="bank-ref-1",
    )
    conn.execute(
        text("UPDATE financial_transactions SET status = 'PAID', settled_at = :at WHERE id = :id"),
        {"at": reimbursed_at, "id": expense},
    )
    return expense, reimbursement


def add_refund(
    conn: Connection,
    fresh: Fresh,
    parent: uuid.UUID,
    parent_kind: str,
    amount: int,
    *,
    status: str = "PENDING",
    settled_at: datetime | None = None,
    reference: str | None = None,
) -> uuid.UUID:
    tx = add_transaction(
        conn,
        fresh,
        kind="REFUND",
        direction="OUT" if parent_kind == "CONTRIBUTION" else "IN",
        amount=amount,
        status=status,
        created_by=fresh.treasurer,
        occurred_at=settled_at,
        settled_at=settled_at,
        parent=parent,
        parent_kind=parent_kind,
    )
    conn.execute(
        text(
            "INSERT INTO refunds (transaction_id, organization_id, school_id, reason, "
            "payment_reference) VALUES (:tx, :org, :school, 'duplicate payment', :ref)"
        ),
        {"tx": tx, "org": fresh.org, "school": fresh.school, "ref": reference},
    )
    return tx


def check_consistency(conn: Connection) -> None:
    """Run the deferred consistency trigger now, on everything this transaction wrote, then go
    back to deferring it (the builders insert a ledger row before its detail row)."""
    conn.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
    conn.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")


def summary(
    conn: Connection, fresh: Fresh, start: date, end: date
) -> tuple[int, int, int, int, int]:
    """(opening, total_in, total_out, closing, entries_count) of the period."""
    row = conn.execute(
        text(
            "SELECT opening_balance_cents, total_in_cents, total_out_cents, closing_balance_cents, "
            "entries_count FROM statement_summary(:school, :start, :end)"
        ),
        {"school": fresh.school, "start": start, "end": end},
    ).one()
    return tuple(row)  # type: ignore[return-value, unused-ignore]


def entries(conn: Connection, fresh: Fresh, start: date, end: date) -> list[Any]:
    return list(
        conn.execute(
            text(
                "SELECT transaction_id, reference_code, kind, direction, signed_amount_cents, "
                "opening_balance_cents, running_balance_cents, late_adjustment, local_date "
                "FROM statement_entries(:school, :start, :end)"
            ),
            {"school": fresh.school, "start": start, "end": end},
        )
    )
