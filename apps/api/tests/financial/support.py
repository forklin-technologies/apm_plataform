"""Builders for the financial tests: a school with people and categories, and the movements of a
ledger. Everything here runs on a connection of the admin of the dev database (a superuser), so the
rows are written the way a migration or a seed would write them: row level security does not get in
the way and EVERY trigger fires (the guards, the booking, the reference code, the audit).

Most tests use these builders inside a transaction that is rolled back, so nothing has to be
cleaned up. The deferred consistency trigger then never runs on its own; call `check_consistency`
to run it on purpose.
"""

# ruff: noqa: E501, S608  (SQL text; the f-string SQL only names columns of this module)

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


DEFAULT_CATEGORIES = (
    # key, name, applies_to, report_group (the defaults of the product document)
    ("parent_contribution", "Contribuição de pais", "IN", "CONTRIBUTIONS"),
    ("donation", "Doações", "IN", "OTHER_INCOME"),
    ("school_supplies", "Compra de material", "OUT", "EXPENSES_REIMBURSEMENTS"),
    ("teacher_reimbursement", "Reembolso de professor", "OUT", "EXPENSES_REIMBURSEMENTS"),
    ("refund", "Devolução", "IN", "REFUNDS"),
)


@dataclass(frozen=True)
class Fresh:
    """A school of its own, with an organization, three members, a payment account and categories."""

    org: uuid.UUID
    school: uuid.UUID
    admin: uuid.UUID  # organization_admin: a membership of the whole organization
    treasurer: uuid.UUID  # a membership of the school
    staff: uuid.UUID  # a membership of the school
    outsider: uuid.UUID  # a user with no membership at all
    account: uuid.UUID  # the ACTIVE payment account of the school
    cat_in: uuid.UUID  # parent_contribution (IN, group CONTRIBUTIONS)
    cat_other: uuid.UUID  # donation (IN, group OTHER_INCOME)
    cat_out: uuid.UUID  # school_supplies (OUT)
    cat_reimb: uuid.UUID  # teacher_reimbursement (OUT)
    cat_refund: uuid.UUID  # refund (IN, group REFUNDS)


def create_categories(conn: Connection, org: uuid.UUID, school: uuid.UUID) -> dict[str, uuid.UUID]:
    """The default categories are installed by the trigger of the school (schools_10_settings): read
    them back, by key."""
    rows = conn.execute(
        text("SELECT key, id FROM categories WHERE school_id = :s AND organization_id = :o"),
        {"s": school, "o": org},
    ).all()
    ids: dict[str, uuid.UUID] = dict(rows)
    missing = [key for key, *_ in DEFAULT_CATEGORIES if key not in ids]
    assert not missing, f"the school was created without its default categories: {missing}"
    return ids


def create_account(
    conn: Connection, org: uuid.UUID, school: uuid.UUID, *, status: str = "ACTIVE"
) -> uuid.UUID:
    account: uuid.UUID = conn.execute(
        text(
            "INSERT INTO payment_accounts (organization_id, school_id, provider, external_account_id, "
            "status, secret_ref, webhook_secret_hash) "
            "VALUES (:o, :s, 'SANDBOX', :e, :st, 'env:T5_SANDBOX_SECRET', :h) RETURNING id"
        ),
        {"o": org, "s": school, "e": uuid.uuid4().hex[:12], "st": status, "h": token_hash()},
    ).scalar_one()
    return account


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
    categories = create_categories(conn, org, school)
    account = create_account(conn, org, school)
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
        account=account,
        cat_in=categories["parent_contribution"],
        cat_other=categories["donation"],
        cat_out=categories["school_supplies"],
        cat_reimb=categories["teacher_reimbursement"],
        cat_refund=categories["refund"],
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
    origin_type: str | None = None,
    origin_name: str | None = None,
    origin_user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    default_category = {
        "CONTRIBUTION": fresh.cat_in,
        "EXPENSE": fresh.cat_out,
        "REIMBURSEMENT": fresh.cat_reimb,
        "REFUND": fresh.cat_refund,
    }[kind]
    default_origin = {
        "CONTRIBUTION": "GUARDIAN",
        "EXPENSE": "TEACHER",
        "REIMBURSEMENT": "TEACHER",
        "REFUND": "TEACHER",
    }[kind]
    return conn.execute(
        text(
            "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, "
            "amount_cents, status, category_id, origin_type, origin_name, origin_user_id, "
            "occurred_at, settled_at, parent_transaction_id, parent_kind, created_by_user_id) "
            "VALUES (:org, :school, :kind, :direction, :amount, :status, :category, :origin_type, "
            ":origin_name, :origin_user, coalesce(:occurred_at, now()), :settled_at, :parent, "
            ":parent_kind, :created_by) RETURNING id"
        ),
        {
            "org": fresh.org,
            "school": fresh.school,
            "kind": kind,
            "direction": direction,
            "amount": amount,
            "status": status,
            "category": category or default_category,
            "origin_type": origin_type or default_origin,
            "origin_name": origin_name,
            "origin_user": origin_user_id,
            "occurred_at": occurred_at,
            "settled_at": settled_at,
            "parent": parent,
            "parent_kind": parent_kind,
            "created_by": created_by,
        },
    ).scalar_one()


def set_status(
    conn: Connection, tx: uuid.UUID, status: str, settled_at: datetime | None = None
) -> None:
    conn.execute(
        text("UPDATE financial_transactions SET status = :s, settled_at = :at WHERE id = :t"),
        {"s": status, "at": settled_at, "t": tx},
    )


def add_cash_contribution(
    conn: Connection,
    fresh: Fresh,
    amount: int,
    settled_at: datetime,
    *,
    guardian: str | None = None,
    student: str | None = None,
    class_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    method: str = "CASH",
    category: uuid.UUID | None = None,
    origin_type: str = "GUARDIAN",
) -> uuid.UUID:
    """A manual contribution (cash, transfer, other) is born PAID and records who entered it."""
    tx = add_transaction(
        conn,
        fresh,
        kind="CONTRIBUTION",
        direction="IN",
        amount=amount,
        status="PAID",
        category=category or fresh.cat_in,
        created_by=fresh.treasurer,
        occurred_at=settled_at,
        settled_at=settled_at,
        origin_type=origin_type,
    )
    conn.execute(
        text(
            "INSERT INTO contributions (transaction_id, organization_id, school_id, method, "
            "guardian_name, student_name, class_name, contributor_email, contributor_phone) "
            "VALUES (:tx, :org, :school, :method, :guardian, :student, :class_name, :email, :phone)"
        ),
        {
            "tx": tx,
            "org": fresh.org,
            "school": fresh.school,
            "method": method,
            "guardian": guardian,
            "student": student,
            "class_name": class_name,
            "email": email,
            "phone": phone,
        },
    )
    return tx


def add_direct_pix(
    conn: Connection,
    fresh: Fresh,
    amount: int,
    *,
    settled_at: datetime | None = None,
    reference: str | None = None,
    guardian: str | None = None,
    category: uuid.UUID | None = None,
) -> uuid.UUID:
    """A Pix paid straight to the key of the APM, outside the platform's charge, registered by the
    treasury. With settled_at it is born PAID; without, it is born REVIEW_REQUIRED (a credit seen on
    the bank statement and not yet confirmed). `reference` is the end-to-end id of the Pix."""
    tx = add_transaction(
        conn,
        fresh,
        kind="CONTRIBUTION",
        direction="IN",
        amount=amount,
        status="PAID" if settled_at is not None else "REVIEW_REQUIRED",
        category=category or fresh.cat_in,
        created_by=fresh.treasurer,
        occurred_at=settled_at,
        settled_at=settled_at,
    )
    conn.execute(
        text(
            "INSERT INTO contributions (transaction_id, organization_id, school_id, method, "
            "external_reference, guardian_name) "
            "VALUES (:tx, :org, :school, 'PIX_DIRECT', :ref, :guardian)"
        ),
        {
            "tx": tx,
            "org": fresh.org,
            "school": fresh.school,
            "ref": reference or end_to_end_id(),
            "guardian": guardian,
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
            "INSERT INTO pix_charges (organization_id, school_id, transaction_id, payment_account_id, "
            "provider, txid, amount_cents, expires_at) "
            "VALUES (:org, :school, :tx, :account, 'SANDBOX', :txid, :amount, "
            "now() + interval '30 minutes') RETURNING id"
        ),
        {
            "org": fresh.org,
            "school": fresh.school,
            "tx": tx,
            "account": fresh.account,
            "txid": uuid.uuid4().hex,
            "amount": amount,
        },
    ).scalar_one()
    if paid_at is not None:
        conn.execute(
            text(
                "UPDATE pix_charges SET status = 'PAID', end_to_end_id = :e2e, paid_at = :paid_at, "
                "received_amount_cents = :amount WHERE id = :id"
            ),
            {"e2e": end_to_end_id(), "paid_at": paid_at, "amount": amount, "id": charge},
        )
        set_status(conn, tx, "PAID", paid_at)
    return tx, charge


def add_pix_review(
    conn: Connection, fresh: Fresh, amount: int, received: int, *, paid_at: datetime | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    """A Pix whose received amount differs from the expected one: the charge is REVIEW_REQUIRED and
    so is the contribution (the management decides). Returns (transaction, charge)."""
    tx, charge = add_pix_contribution(conn, fresh, amount)
    conn.execute(
        text(
            "UPDATE pix_charges SET status = 'REVIEW_REQUIRED', end_to_end_id = :e2e, "
            "paid_at = :at, received_amount_cents = :r, divergence_reason = 'received amount differs' "
            "WHERE id = :id"
        ),
        {
            "e2e": end_to_end_id(),
            "at": paid_at or utc(2025, 3, 10, 15),
            "r": received,
            "id": charge,
        },
    )
    set_status(conn, tx, "REVIEW_REQUIRED")
    return tx, charge


def add_attachment(
    conn: Connection, fresh: Fresh, expense: uuid.UUID, kind: str = "INVOICE"
) -> uuid.UUID:
    attachment: uuid.UUID = conn.execute(
        text(
            "INSERT INTO expense_attachments (organization_id, school_id, transaction_id, kind, "
            "storage_key, file_name, content_type, size_bytes, sha256, uploaded_by_user_id) "
            "VALUES (:o, :s, :t, :kind, :key, 'nota.pdf', 'application/pdf', 1024, :h, :u) RETURNING id"
        ),
        {
            "o": fresh.org,
            "s": fresh.school,
            "t": expense,
            "kind": kind,
            "key": f"k/{uuid.uuid4().hex}",
            "h": token_hash(),
            "u": fresh.staff,
        },
    ).scalar_one()
    return attachment


def add_expense(
    conn: Connection,
    fresh: Fresh,
    amount: int,
    *,
    paid_by: str = "APM",
    status: str = "SUBMITTED",
    settled_at: datetime | None = None,
    occurred_at: datetime | None = None,
    approved_amount: int | None = None,
    attach: bool = True,
    description: str = "Material",
) -> uuid.UUID:
    """An expense submitted by `staff` and decided by `treasurer` (never the same person).

    It is created the way the application creates it (DRAFT or SUBMITTED) and then taken through
    the state machine to `status`, writing what each step records. Everything beyond DRAFT carries
    an attachment (the deferred consistency check requires one)."""
    start = "DRAFT" if status in ("DRAFT", "CANCELLED") else "SUBMITTED"
    tx = add_transaction(
        conn,
        fresh,
        kind="EXPENSE",
        direction="OUT",
        amount=amount,
        status=start,
        created_by=fresh.staff,
        occurred_at=occurred_at,
        origin_user_id=fresh.staff,
    )
    conn.execute(
        text(
            "INSERT INTO expenses (transaction_id, organization_id, school_id, description, "
            "purchase_reason, payment_method, paid_by, submitted_by_user_id) "
            "VALUES (:tx, :org, :school, :description, 'school project', 'CARD', :paid_by, :staff)"
        ),
        {
            "tx": tx,
            "org": fresh.org,
            "school": fresh.school,
            "paid_by": paid_by,
            "staff": fresh.staff,
            "description": description,
        },
    )
    if attach and start != "DRAFT":
        add_attachment(conn, fresh, tx)
    if status == "CANCELLED":
        set_status(conn, tx, "CANCELLED")
    elif status == "CORRECTION_REQUESTED":
        conn.execute(
            text(
                "UPDATE expenses SET correction_reason = 'attach the receipt' WHERE transaction_id = :t"
            ),
            {"t": tx},
        )
        set_status(conn, tx, "CORRECTION_REQUESTED")
    elif status in ("APPROVED", "REJECTED", "PAID"):
        conn.execute(
            text(
                "UPDATE expenses SET approved_by_user_id = :u, approved_amount_cents = :a, "
                "decision_reason = :r WHERE transaction_id = :t"
            ),
            {
                "u": fresh.treasurer,
                "a": None if status == "REJECTED" else (approved_amount or amount),
                "r": "no budget" if status == "REJECTED" else None,
                "t": tx,
            },
        )
        set_status(conn, tx, "REJECTED" if status == "REJECTED" else "APPROVED")
        if status == "PAID":
            set_status(conn, tx, "PAID", settled_at)
    return tx


def bank_fees_category(conn: Connection, fresh: Fresh) -> uuid.UUID:
    """The category of the bank fees of the school (OUT, group BANK_FEES, no approval), made on first use."""
    found = conn.execute(
        text("SELECT id FROM categories WHERE school_id = :s AND key = 'bank_fees'"),
        {"s": fresh.school},
    ).scalar_one_or_none()
    if found is not None:
        return found
    return conn.execute(
        text(
            "INSERT INTO categories (organization_id, school_id, key, name, applies_to, report_group, "
            "requires_approval) VALUES (:o, :s, 'bank_fees', 'Tarifas bancárias', 'OUT', 'BANK_FEES', false) "
            "RETURNING id"
        ),
        {"o": fresh.org, "s": fresh.school},
    ).scalar_one()


def add_bank_fee(
    conn: Connection,
    fresh: Fresh,
    amount: int,
    *,
    settled_at: datetime | None = None,
    occurred_at: datetime | None = None,
    description: str = "Tarifa Pix Enviado",
) -> uuid.UUID:
    """A bank fee, the way the treasury records it: an expense of the APM in the category without
    approval, born APPROVED (no approver, no attachment) and, with settled_at, settled by whoever
    recorded it. The database fills in the approved amount."""
    tx = add_transaction(
        conn,
        fresh,
        kind="EXPENSE",
        direction="OUT",
        amount=amount,
        status="APPROVED",
        category=bank_fees_category(conn, fresh),
        created_by=fresh.treasurer,
        occurred_at=occurred_at or settled_at,
        origin_type="BANK",
        origin_name="Banco",
    )
    conn.execute(
        text(
            "INSERT INTO expenses (transaction_id, organization_id, school_id, description, paid_by, "
            "submitted_by_user_id) VALUES (:tx, :org, :school, :description, 'APM', :who)"
        ),
        {
            "tx": tx,
            "org": fresh.org,
            "school": fresh.school,
            "description": description,
            "who": fresh.treasurer,
        },
    )
    if settled_at is not None:
        set_status(conn, tx, "PAID", settled_at)
    return tx


def add_reimbursement(
    conn: Connection,
    fresh: Fresh,
    expense: uuid.UUID,
    amount: int | None = None,
    *,
    status: str = "PENDING",
    settled_at: datetime | None = None,
    reference: str | None = None,
    occurred_at: datetime | None = None,
) -> uuid.UUID:
    """A reimbursement of an APPROVED collaborator expense, for the APPROVED amount (the default)."""
    if amount is None:
        amount = conn.execute(
            text("SELECT approved_amount_cents FROM expenses WHERE transaction_id = :t"),
            {"t": expense},
        ).scalar_one()
    tx = add_transaction(
        conn,
        fresh,
        kind="REIMBURSEMENT",
        direction="OUT",
        amount=amount,
        status="PENDING",
        created_by=fresh.treasurer,
        occurred_at=occurred_at or settled_at,
        parent=expense,
        parent_kind="EXPENSE",
        origin_user_id=fresh.staff,
    )
    conn.execute(
        text(
            "INSERT INTO reimbursements (transaction_id, organization_id, school_id, "
            "beneficiary_user_id) VALUES (:tx, :org, :school, :staff)"
        ),
        {"tx": tx, "org": fresh.org, "school": fresh.school, "staff": fresh.staff},
    )
    if status == "PAID":
        conn.execute(
            text(
                "UPDATE reimbursements SET payment_reference = :r, paid_by_user_id = :u "
                "WHERE transaction_id = :t"
            ),
            {"r": reference or "bank-ref-1", "u": fresh.treasurer, "t": tx},
        )
        set_status(conn, tx, "PAID", settled_at)
    elif status == "CANCELLED":
        set_status(conn, tx, "CANCELLED")
    return tx


def add_collaborator_expense(
    conn: Connection,
    fresh: Fresh,
    amount: int,
    *,
    approved_amount: int | None = None,
    reimbursed_at: datetime | None = None,
    occurred_at: datetime | None = None,
    description: str = "Material",
) -> tuple[uuid.UUID, uuid.UUID]:
    """An expense paid by a collaborator, approved (maybe partially), and its reimbursement (PAID
    when reimbursed_at is given). The expense itself never moves the cash. Returns
    (expense, reimbursement)."""
    expense = add_expense(
        conn,
        fresh,
        amount,
        paid_by="COLLABORATOR",
        status="APPROVED",
        approved_amount=approved_amount,
        occurred_at=occurred_at,
        description=description,
    )
    if reimbursed_at is None:
        return expense, add_reimbursement(conn, fresh, expense, occurred_at=occurred_at)
    reimbursement = add_reimbursement(
        conn, fresh, expense, status="PAID", settled_at=reimbursed_at, reference="bank-ref-1"
    )
    set_status(conn, expense, "PAID", reimbursed_at)
    return expense, reimbursement


def add_refund(
    conn: Connection,
    fresh: Fresh,
    amount: int,
    *,
    parent: uuid.UUID | None = None,
    parent_kind: str | None = None,
    status: str = "REQUESTED",
    settled_at: datetime | None = None,
    reason: str = "unused balance",
    origin_type: str = "TEACHER",
    origin_user_id: uuid.UUID | None = None,
    origin_name: str | None = None,
) -> uuid.UUID:
    """A devolução: money that comes BACK to the APM. The parent (an expense or a reimbursement) is
    optional. Taken through REQUESTED, AWAITING_CONFIRMATION and CONFIRMED (or REJECTED)."""
    tx = add_transaction(
        conn,
        fresh,
        kind="REFUND",
        direction="IN",
        amount=amount,
        status="REQUESTED",
        created_by=fresh.treasurer,
        occurred_at=settled_at,
        parent=parent,
        parent_kind=parent_kind,
        origin_type=origin_type,
        origin_user_id=origin_user_id,
        origin_name=origin_name,
    )
    conn.execute(
        text(
            "INSERT INTO refunds (transaction_id, organization_id, school_id, reason) "
            "VALUES (:tx, :org, :school, :reason)"
        ),
        {"tx": tx, "org": fresh.org, "school": fresh.school, "reason": reason},
    )
    if status in ("AWAITING_CONFIRMATION", "CONFIRMED"):
        set_status(conn, tx, "AWAITING_CONFIRMATION")
    if status == "CONFIRMED":
        conn.execute(
            text("UPDATE refunds SET confirmed_by_user_id = :u WHERE transaction_id = :t"),
            {"u": fresh.treasurer, "t": tx},
        )
        set_status(conn, tx, "CONFIRMED", settled_at)
    elif status == "REJECTED":
        set_status(conn, tx, "REJECTED")
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
    row = summary_row(conn, fresh, start, end)
    return (
        row.opening_balance_cents,
        row.total_in_cents,
        row.total_out_cents,
        row.closing_balance_cents,
        row.entries_count,
    )


def summary_row(conn: Connection, fresh: Fresh, start: date, end: date) -> Any:
    """The whole statement_summary row (every figure, both balances)."""
    return conn.execute(
        text("SELECT * FROM statement_summary(:school, :start, :end)"),
        {"school": fresh.school, "start": start, "end": end},
    ).one()


def entries(conn: Connection, fresh: Fresh, start: date, end: date, **filters: Any) -> list[Any]:
    """statement_entries rows; filters: p_type, p_category, p_status, p_person."""
    names = ", ".join(f":{name}" for name in ("school", "start", "end", *filters))
    arguments = {"school": fresh.school, "start": start, "end": end, **filters}
    sql = f"SELECT * FROM statement_entries({names})"
    if filters:  # named notation, so that the optional filters can be given alone
        named = ", ".join(f"{key} => :{key}" for key in filters)
        sql = f"SELECT * FROM statement_entries(:school, :start, :end, {named})"
    return list(conn.execute(text(sql), arguments))
