"""Fixtures of the financial tests.

`ledger` writes a small ledger into the CONFIGURED database (like the `tenants` fixture of the
tenancy tests, so that dropping a policy or removing FORCE makes the isolation tests fail) and the
`tenants` fixture removes it again, financial rows included.
"""

# ruff: noqa: E501  (SQL text)

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, fields
from datetime import date

import pytest
from sqlalchemy import Connection, Engine, text

from tests.dbsupport import Tenants
from tests.financial.support import (
    Fresh,
    add_cash_contribution,
    add_collaborator_expense,
    add_expense,
    add_pix_contribution,
    add_refund,
    end_to_end_id,
    make_school,
    token_hash,
    utc,
)


@dataclass(frozen=True)
class Ledger:
    """Rows of the schools of the `tenants` fixture. School A1 is the subject of the matrix."""

    fresh: Fresh  # a wrapper so the builders can be reused: org A, school A1, members of A1
    school_a3: uuid.UUID  # a school of organization A with NO settings row (settings insert test)
    # financial_transactions of A1
    cash_contribution: uuid.UUID  # PAID, CASH
    pix_pending: uuid.UUID  # PENDING_PAYMENT, PIX, with a PENDING charge
    pix_pending_2: uuid.UUID  # PENDING_PAYMENT, PIX, without a charge
    pix_charge: uuid.UUID
    expense_submitted: uuid.UUID
    expense_paid: uuid.UUID  # APM, PAID
    expense_collaborator: uuid.UUID  # COLLABORATOR, APPROVED
    reimbursement_pending: uuid.UUID
    refund_pending: uuid.UUID  # of cash_contribution
    bare_contribution: uuid.UUID  # ft rows WITHOUT their detail row (targets of the insert matrix)
    bare_expense: uuid.UUID
    bare_reimbursement: uuid.UUID
    bare_refund: uuid.UUID
    cancelled_contribution: uuid.UUID  # terminal
    rejected_expense: uuid.UUID  # terminal
    failed_refund: uuid.UUID  # terminal
    expired_charge: uuid.UUID  # terminal
    attachment: uuid.UUID
    webhook_event: uuid.UUID
    audit_log: uuid.UUID
    category: uuid.UUID
    closing: uuid.UUID  # a closing of A1 (two months ago)
    # a row of the other schools, to prove the other contexts cannot see or touch them
    school_a2_transaction: uuid.UUID
    school_b1_transaction: uuid.UUID
    school_b1_category: uuid.UUID
    school_b1_webhook_event: uuid.UUID
    bare_ids: tuple[uuid.UUID, ...]

    def all_ids(self) -> list[uuid.UUID]:
        return [v for f in fields(self) if isinstance(v := getattr(self, f.name), uuid.UUID)]


def _bare_transaction(
    conn: Connection,
    fresh: Fresh,
    *,
    kind: str,
    direction: str,
    status: str,
    reference: int,
    parent: uuid.UUID | None = None,
    parent_kind: str | None = None,
    category: uuid.UUID | None = None,
) -> uuid.UUID:
    """A ledger row without its detail row. Triggers are off for it (replica mode) so that the
    reference code is explicit and the deferred consistency check does not apply."""
    conn.execute(text("SET LOCAL session_replication_role = replica"))
    try:
        return conn.execute(
            text(
                "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, "
                "amount_cents, status, category_id, parent_transaction_id, parent_kind, "
                "created_by_user_id, reference_code) "
                "VALUES (:org, :school, :kind, :direction, 700, :status, :category, :parent, "
                ":parent_kind, :staff, :reference) RETURNING id"
            ),
            {
                "org": fresh.org,
                "school": fresh.school,
                "kind": kind,
                "direction": direction,
                "status": status,
                "category": category,
                "parent": parent,
                "parent_kind": parent_kind,
                "staff": fresh.staff,
                "reference": reference,
            },
        ).scalar_one()
    finally:
        conn.execute(text("RESET session_replication_role"))


@pytest.fixture(scope="session")
def ledger(admin_engine: Engine, tenants: Tenants) -> Iterator[Ledger]:
    """Needs the Tenants of the tenancy tests: org A (schools A1, A2) and org B (school B1)."""
    a1 = Fresh(
        org=tenants.org_a,
        school=tenants.school_a1,
        admin=tenants.user_admin_a,  # org-wide membership of A
        treasurer=tenants.user_admin_a,  # decides (the submitter is another user)
        staff=tenants.user_a1,  # staff membership of A1
        outsider=tenants.user_free,
        cat_in=uuid.uuid4(),
        cat_out=uuid.uuid4(),
    )
    now = utc(2026, 1, 1)
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO categories (id, organization_id, school_id, key, name, applies_to) "
                "VALUES (:i, :org, :school, 't5_in', 'In', 'IN'), (:o, :org, :school, 't5_out', 'Out', 'OUT')"
            ),
            {"i": a1.cat_in, "o": a1.cat_out, "org": a1.org, "school": a1.school},
        )
        school_a3 = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO schools (id, organization_id, name, slug) "
                "VALUES (:id, :org, 'School A3', :slug)"
            ),
            {"id": school_a3, "org": tenants.org_a, "slug": f"t5-a3-{uuid.uuid4().hex[:10]}"},
        )
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        conn.execute(text("DELETE FROM school_settings WHERE school_id = :s"), {"s": school_a3})
        conn.execute(text("RESET session_replication_role"))

        cash = add_cash_contribution(conn, a1, 5000, now)
        pix_pending, pix_charge = add_pix_contribution(conn, a1, 3000)
        pix_pending_2, _ = add_pix_contribution(conn, a1, 3100)
        # The second charge of pix_pending_2 would be a second PENDING one: drop it from the way
        # by expiring it, so the insert matrix can create a charge for it.
        conn.execute(
            text("UPDATE pix_charges SET status = 'EXPIRED' WHERE transaction_id = :t"),
            {"t": pix_pending_2},
        )
        expense_submitted = add_expense(conn, a1, 1500)
        expense_paid = add_expense(conn, a1, 2500, status="PAID", settled_at=now)
        expense_collaborator, reimbursement_pending = add_collaborator_expense(conn, a1, 4000)
        refund_pending = add_refund(conn, a1, cash, "CONTRIBUTION", 1000)
        cancelled = add_pix_contribution(conn, a1, 900)[0]
        conn.execute(
            text("UPDATE financial_transactions SET status = 'CANCELLED' WHERE id = :t"),
            {"t": cancelled},
        )
        rejected = add_expense(conn, a1, 800)
        conn.execute(
            text(
                "UPDATE expenses SET approved_by_user_id = :u, decision_reason = 'no budget' WHERE transaction_id = :t"
            ),
            {"u": a1.treasurer, "t": rejected},
        )
        conn.execute(
            text("UPDATE financial_transactions SET status = 'REJECTED' WHERE id = :t"),
            {"t": rejected},
        )
        failed_refund = add_refund(conn, a1, cash, "CONTRIBUTION", 500)
        conn.execute(
            text("UPDATE financial_transactions SET status = 'FAILED' WHERE id = :t"),
            {"t": failed_refund},
        )
        expired_charge: uuid.UUID = conn.execute(
            text("SELECT id FROM pix_charges WHERE transaction_id = :t"), {"t": pix_pending_2}
        ).scalar_one()

        # Only one active reimbursement per expense: the bare one hangs from another expense.
        expense_collaborator_2 = add_expense(
            conn, a1, 4100, paid_by="COLLABORATOR", status="APPROVED"
        )
        bare_contribution = _bare_transaction(
            conn, a1, kind="CONTRIBUTION", direction="IN", status="PENDING_PAYMENT", reference=9901
        )
        bare_expense = _bare_transaction(
            conn,
            a1,
            kind="EXPENSE",
            direction="OUT",
            status="SUBMITTED",
            reference=9902,
            category=a1.cat_out,
        )
        bare_reimbursement = _bare_transaction(
            conn,
            a1,
            kind="REIMBURSEMENT",
            direction="OUT",
            status="PENDING",
            reference=9903,
            parent=expense_collaborator_2,
            parent_kind="EXPENSE",
        )
        bare_refund = _bare_transaction(
            conn,
            a1,
            kind="REFUND",
            direction="OUT",
            status="PENDING",
            reference=9904,
            parent=cash,
            parent_kind="CONTRIBUTION",
        )

        attachment: uuid.UUID = conn.execute(
            text(
                "INSERT INTO expense_attachments (organization_id, school_id, transaction_id, "
                "storage_key, file_name, content_type, size_bytes, sha256, uploaded_by_user_id) "
                "VALUES (:org, :school, :tx, 'k/' || :k, 'nota.pdf', 'application/pdf', 1024, :h, :u) "
                "RETURNING id"
            ),
            {
                "org": a1.org,
                "school": a1.school,
                "tx": expense_submitted,
                "k": uuid.uuid4().hex,
                "h": token_hash(),
                "u": a1.staff,
            },
        ).scalar_one()
        webhook: uuid.UUID = conn.execute(
            text(
                "INSERT INTO webhook_events (organization_id, school_id, provider, idempotency_key, "
                "end_to_end_id, raw_payload, signature_valid) "
                "VALUES (:org, :school, 'SANDBOX', :k, :e2e, '{\"pix\": []}'::jsonb, true) RETURNING id"
            ),
            {"org": a1.org, "school": a1.school, "k": uuid.uuid4().hex, "e2e": end_to_end_id()},
        ).scalar_one()
        audit_log: uuid.UUID = conn.execute(
            text(
                "INSERT INTO audit_logs (organization_id, school_id, action, entity_type) "
                "VALUES (:org, :school, 'test.event', 'test') RETURNING id"
            ),
            {"org": a1.org, "school": a1.school},
        ).scalar_one()
        # A closing of A1 for the month two months ago (it has no entries: nothing was settled then).
        conn.execute(text("SET LOCAL session_replication_role = origin"))
        closing_month: date = conn.execute(
            text(
                "SELECT (date_trunc('month', (now() AT TIME ZONE 'America/Sao_Paulo')) "
                "- interval '2 months')::date"
            )
        ).scalar_one()
        closing: uuid.UUID = conn.execute(
            text(
                "INSERT INTO monthly_closings (organization_id, school_id, period_start, "
                "closed_by_user_id) VALUES (:org, :school, :start, :u) RETURNING id"
            ),
            {"org": a1.org, "school": a1.school, "start": closing_month, "u": a1.treasurer},
        ).scalar_one()

        # Rows of the other schools.
        a2 = Fresh(
            org=tenants.org_a,
            school=tenants.school_a2,
            admin=tenants.user_admin_a,
            treasurer=tenants.user_admin_a,
            staff=tenants.user_a2,
            outsider=tenants.user_free,
            cat_in=uuid.uuid4(),
            cat_out=uuid.uuid4(),
        )
        b1 = Fresh(
            org=tenants.org_b,
            school=tenants.school_b1,
            admin=tenants.user_b1,
            treasurer=tenants.user_b1,
            staff=tenants.user_b1,
            outsider=tenants.user_free,
            cat_in=uuid.uuid4(),
            cat_out=uuid.uuid4(),
        )
        for other in (a2, b1):
            conn.execute(
                text(
                    "INSERT INTO categories (id, organization_id, school_id, key, name, applies_to) "
                    "VALUES (:i, :org, :school, 't5_in', 'In', 'IN'), (:o, :org, :school, 't5_out', 'Out', 'OUT')"
                ),
                {"i": other.cat_in, "o": other.cat_out, "org": other.org, "school": other.school},
            )
        a2_tx = add_cash_contribution(conn, a2, 100, now)
        b1_tx = add_cash_contribution(conn, b1, 200, now)
        b1_webhook: uuid.UUID = conn.execute(
            text(
                "INSERT INTO webhook_events (organization_id, school_id, provider, idempotency_key, "
                "raw_payload, signature_valid) VALUES (:org, :school, 'SANDBOX', :k, '{}'::jsonb, true) "
                "RETURNING id"
            ),
            {"org": b1.org, "school": b1.school, "k": uuid.uuid4().hex},
        ).scalar_one()
        # make_school-style helper not needed for the others: they only need a row to be hidden.
        _ = make_school  # noqa: F841 (kept for tests that import it from here)

    yield Ledger(
        fresh=a1,
        school_a3=school_a3,
        cash_contribution=cash,
        pix_pending=pix_pending,
        pix_pending_2=pix_pending_2,
        pix_charge=pix_charge,
        expense_submitted=expense_submitted,
        expense_paid=expense_paid,
        expense_collaborator=expense_collaborator,
        reimbursement_pending=reimbursement_pending,
        refund_pending=refund_pending,
        bare_contribution=bare_contribution,
        bare_expense=bare_expense,
        bare_reimbursement=bare_reimbursement,
        bare_refund=bare_refund,
        cancelled_contribution=cancelled,
        rejected_expense=rejected,
        failed_refund=failed_refund,
        expired_charge=expired_charge,
        attachment=attachment,
        webhook_event=webhook,
        audit_log=audit_log,
        category=a1.cat_in,
        closing=closing,
        school_a2_transaction=a2_tx,
        school_b1_transaction=b1_tx,
        school_b1_category=b1.cat_in,
        school_b1_webhook_event=b1_webhook,
        bare_ids=(bare_contribution, bare_expense, bare_reimbursement, bare_refund),
    )
