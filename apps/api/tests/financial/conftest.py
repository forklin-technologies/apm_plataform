# ruff: noqa: E501  (SQL text)
"""Fixtures of the financial tests.

`ledger` writes a small ledger into the CONFIGURED database (like the `tenants` fixture of the
tenancy tests, so that dropping a policy or removing FORCE makes the isolation tests fail) and the
`tenants` fixture removes it again, financial rows included.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, fields
from datetime import date

import pytest
from sqlalchemy import Connection, Engine, text

from tests.dbsupport import Tenants
from tests.financial.support import (
    Fresh,
    add_attachment,
    add_cash_contribution,
    add_collaborator_expense,
    add_expense,
    add_pix_contribution,
    add_pix_review,
    add_refund,
    create_account,
    create_categories,
    end_to_end_id,
    make_school,
    set_status,
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
    pix_pending_2: uuid.UUID  # PENDING_PAYMENT, PIX, whose charge expired
    pix_review: uuid.UUID  # REVIEW_REQUIRED, PIX (received amount differs)
    pix_charge: uuid.UUID
    expense_draft: uuid.UUID  # DRAFT (editable)
    expense_submitted: uuid.UUID
    expense_paid: uuid.UUID  # APM, PAID
    expense_collaborator: uuid.UUID  # COLLABORATOR, APPROVED
    reimbursement_pending: uuid.UUID
    refund_requested: uuid.UUID  # a devolução, REQUESTED, with no parent
    bare_contribution: uuid.UUID  # ft rows WITHOUT their detail row (targets of the insert matrix)
    bare_expense: uuid.UUID
    bare_reimbursement: uuid.UUID
    bare_refund: uuid.UUID
    cancelled_contribution: uuid.UUID  # terminal
    rejected_expense: uuid.UUID  # terminal
    rejected_refund: uuid.UUID  # terminal
    expired_charge: uuid.UUID  # terminal
    attachment: uuid.UUID
    webhook_event: uuid.UUID
    audit_log: uuid.UUID
    payment_account: uuid.UUID
    category: uuid.UUID
    closing: uuid.UUID  # a closing of A1 (two months ago)
    # a row of the other schools, to prove the other contexts cannot see or touch them
    school_a2_transaction: uuid.UUID
    school_b1_transaction: uuid.UUID
    school_b1_category: uuid.UUID
    school_b1_webhook_event: uuid.UUID
    school_b1_account: uuid.UUID
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
    category: uuid.UUID,
    parent: uuid.UUID | None = None,
    parent_kind: str | None = None,
) -> uuid.UUID:
    """A ledger row without its detail row. Triggers are off for it (replica mode) so that the
    reference code is explicit and the deferred consistency check does not apply."""
    conn.execute(text("SET LOCAL session_replication_role = replica"))
    row: uuid.UUID = conn.execute(
        text(
            "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, "
            "amount_cents, status, category_id, origin_type, parent_transaction_id, parent_kind, "
            "created_by_user_id, reference_code) "
            "VALUES (:org, :school, :kind, :direction, 700, :status, :category, 'TEACHER', :parent, "
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
    conn.execute(text("RESET session_replication_role"))
    return row


@pytest.fixture(scope="session")
def ledger(admin_engine: Engine, tenants: Tenants) -> Iterator[Ledger]:
    """Needs the Tenants of the tenancy tests: org A (schools A1, A2) and org B (school B1)."""
    with admin_engine.begin() as conn:
        categories_a1 = create_categories(conn, tenants.org_a, tenants.school_a1)
        account_a1 = create_account(conn, tenants.org_a, tenants.school_a1)
        a1 = Fresh(
            org=tenants.org_a,
            school=tenants.school_a1,
            admin=tenants.user_admin_a,  # org-wide membership of A
            treasurer=tenants.user_admin_a,  # decides (the submitter is another user)
            staff=tenants.user_a1,  # staff membership of A1
            outsider=tenants.user_free,
            account=account_a1,
            cat_in=categories_a1["parent_contribution"],
            cat_other=categories_a1["donation"],
            cat_out=categories_a1["school_supplies"],
            cat_reimb=categories_a1["teacher_reimbursement"],
            cat_refund=categories_a1["refund"],
        )
        now = utc(2026, 1, 1)
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
        pix_pending_2, expired_charge = add_pix_contribution(conn, a1, 3100)
        # Expire the charge of the second one, so the insert matrix can create another for it.
        conn.execute(
            text("UPDATE pix_charges SET status = 'EXPIRED' WHERE id = :c"), {"c": expired_charge}
        )
        pix_review, _ = add_pix_review(conn, a1, 3300, 3350)
        expense_draft = add_expense(conn, a1, 1700, status="DRAFT")
        expense_submitted = add_expense(conn, a1, 1500)
        expense_paid = add_expense(conn, a1, 2500, status="PAID", settled_at=now)
        expense_collaborator, reimbursement_pending = add_collaborator_expense(conn, a1, 4000)
        refund_requested = add_refund(conn, a1, 100)
        cancelled = add_pix_contribution(conn, a1, 900)[0]
        set_status(conn, cancelled, "CANCELLED")
        rejected = add_expense(conn, a1, 800, status="REJECTED")
        rejected_refund = add_refund(conn, a1, 500, status="REJECTED")
        # Only one active reimbursement per expense: the bare one hangs from another expense.
        expense_collaborator_2 = add_expense(
            conn, a1, 4100, paid_by="COLLABORATOR", status="APPROVED"
        )
        bare_contribution = _bare_transaction(
            conn, a1, kind="CONTRIBUTION", direction="IN", status="PENDING_PAYMENT",
            reference=9901, category=a1.cat_in,
        )  # fmt: skip
        bare_expense = _bare_transaction(
            conn, a1, kind="EXPENSE", direction="OUT", status="SUBMITTED", reference=9902,
            category=a1.cat_out,
        )  # fmt: skip
        bare_reimbursement = _bare_transaction(
            conn, a1, kind="REIMBURSEMENT", direction="OUT", status="PENDING", reference=9903,
            category=a1.cat_reimb, parent=expense_collaborator_2, parent_kind="EXPENSE",
        )  # fmt: skip
        bare_refund = _bare_transaction(
            conn, a1, kind="REFUND", direction="IN", status="REQUESTED", reference=9904,
            category=a1.cat_refund,
        )  # fmt: skip

        attachment = add_attachment(conn, a1, expense_submitted, "PAYMENT_PROOF")
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
        categories_a2 = create_categories(conn, tenants.org_a, tenants.school_a2)
        account_a2 = create_account(conn, tenants.org_a, tenants.school_a2)
        a2 = Fresh(
            org=tenants.org_a, school=tenants.school_a2, admin=tenants.user_admin_a,
            treasurer=tenants.user_admin_a, staff=tenants.user_a2, outsider=tenants.user_free,
            account=account_a2, cat_in=categories_a2["parent_contribution"],
            cat_other=categories_a2["donation"], cat_out=categories_a2["school_supplies"],
            cat_reimb=categories_a2["teacher_reimbursement"], cat_refund=categories_a2["refund"],
        )  # fmt: skip
        categories_b1 = create_categories(conn, tenants.org_b, tenants.school_b1)
        account_b1 = create_account(conn, tenants.org_b, tenants.school_b1)
        b1 = Fresh(
            org=tenants.org_b, school=tenants.school_b1, admin=tenants.user_b1,
            treasurer=tenants.user_b1, staff=tenants.user_b1, outsider=tenants.user_free,
            account=account_b1, cat_in=categories_b1["parent_contribution"],
            cat_other=categories_b1["donation"], cat_out=categories_b1["school_supplies"],
            cat_reimb=categories_b1["teacher_reimbursement"], cat_refund=categories_b1["refund"],
        )  # fmt: skip
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
        _ = make_school  # noqa: F841 (kept for tests that import it from here)

    yield Ledger(
        fresh=a1, school_a3=school_a3, cash_contribution=cash, pix_pending=pix_pending,
        pix_pending_2=pix_pending_2, pix_review=pix_review, pix_charge=pix_charge,
        expense_draft=expense_draft, expense_submitted=expense_submitted,
        expense_paid=expense_paid, expense_collaborator=expense_collaborator,
        reimbursement_pending=reimbursement_pending, refund_requested=refund_requested,
        bare_contribution=bare_contribution, bare_expense=bare_expense,
        bare_reimbursement=bare_reimbursement, bare_refund=bare_refund,
        cancelled_contribution=cancelled, rejected_expense=rejected,
        rejected_refund=rejected_refund, expired_charge=expired_charge, attachment=attachment,
        webhook_event=webhook, audit_log=audit_log, payment_account=account_a1,
        category=a1.cat_in, closing=closing, school_a2_transaction=a2_tx,
        school_b1_transaction=b1_tx, school_b1_category=b1.cat_in,
        school_b1_webhook_event=b1_webhook, school_b1_account=account_b1,
        bare_ids=(bare_contribution, bare_expense, bare_reimbursement, bare_refund),
    )  # fmt: skip


@pytest.fixture
def world(admin_engine: Engine) -> Iterator[tuple[Connection, Fresh]]:
    """A transaction of the admin (a superuser: every trigger fires, row level security does not
    apply) with a school of its own. Always rolled back, so nothing is left behind."""
    from tests.dbsupport import transaction

    with transaction(admin_engine) as conn:
        yield conn, make_school(conn)
