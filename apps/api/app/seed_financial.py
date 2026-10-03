# ruff: noqa: E501  (SQL text)
"""Financial part of the development seed. FAKE data only (the caller refuses ENV=production).

Three things:
  * the default categories of every school (keys in English, names in Portuguese, each with its
    report group), which a school created later gets from the service that creates it
    (DEFAULT_CATEGORIES is the list to use);
  * a demonstration payment account per school, WITHOUT any credential (secret_ref is only a
    reference to a secrets manager that does not exist in development);
  * demonstration movements for each demo school that has none yet: contributions in cash, by
    transfer and by Pix (paid, pending and in review, and one paid straight to the key of the APM),
    expenses in every state, a bank fee, reimbursements, a devolução. They are written the way the application will write them, so every trigger of the
    schema runs (state machines, reference codes, audit, consistency at commit), and the actor of
    the audit records is SYSTEM.

It runs as the admin of the development database (a superuser, so row level security does not
get in the way) and is safe to run twice.
"""

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import Connection, text

# (key, name, applies to, report group). Keys are stable; names are what the school sees.
DEFAULT_CATEGORIES: tuple[tuple[str, str, str, str], ...] = (
    ("parent_contribution", "Contribuição de pais", "IN", "CONTRIBUTIONS"),
    ("donation", "Doações", "IN", "OTHER_INCOME"),
    ("other_income", "Outras entradas", "IN", "OTHER_INCOME"),
    ("apm_revenue", "Receitas da APM", "IN", "OTHER_INCOME"),
    ("teacher_reimbursement", "Reembolso de professor", "OUT", "EXPENSES_REIMBURSEMENTS"),
    ("director_reimbursement", "Reembolso de diretor", "OUT", "EXPENSES_REIMBURSEMENTS"),
    ("school_supplies", "Compra de material", "OUT", "EXPENSES_REIMBURSEMENTS"),
    ("services", "Serviços", "OUT", "EXPENSES_REIMBURSEMENTS"),
    ("other_authorized", "Outras despesas autorizadas", "OUT", "EXPENSES_REIMBURSEMENTS"),
    ("bank_fees", "Tarifas bancárias", "OUT", "BANK_FEES"),
    ("refund", "Devolução", "IN", "REFUNDS"),
)

# The categories whose expenses need no approver (the bank fees: the bank already took the money).
NO_APPROVAL_CATEGORIES = frozenset({"bank_fees"})

# school slug -> (who enters cash and decides, who submits expenses). Two different people, both
# active members whose membership covers the school (the schema checks it).
DEMO_PEOPLE = {
    "demo-aurora": ("carla.tesoureira@example.test", "bruno.diretor@example.test"),
    "demo-horizonte": ("ana.admin@example.test", "diego.professor@example.test"),
    "demo-central": ("fabio.admin@example.test", "gina.tesoureira@example.test"),
}

MOVEMENTS_PER_SCHOOL = 20

Count = Callable[[str, int], None]


def _user(connection: Connection, email: str) -> uuid.UUID:
    found: Any = connection.execute(
        text("SELECT id FROM users WHERE lower(email) = lower(:e)"), {"e": email}
    ).scalar_one()
    return uuid.UUID(str(found))


def seed_financial(connection: Connection, count: Count) -> None:
    """Add what is missing. `count(table, rows)` reports what this run created."""
    connection.execute(text("SELECT set_config('app.actor_type', 'SYSTEM', true)"))
    schools = connection.execute(
        text("SELECT s.id, s.organization_id, s.slug FROM schools s WHERE s.slug LIKE 'demo-%'")
    ).all()
    for school_id, organization_id, slug in schools:
        created = 0
        for key, name, applies_to, group in DEFAULT_CATEGORIES:
            created += connection.execute(
                text(
                    "INSERT INTO categories (organization_id, school_id, key, name, applies_to, "
                    "report_group, requires_approval) VALUES (:o, :s, :k, :n, :a, :g, :r) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "o": organization_id,
                    "s": school_id,
                    "k": key,
                    "n": name,
                    "a": applies_to,
                    "g": group,
                    "r": key not in NO_APPROVAL_CATEGORIES,
                },
            ).rowcount
        count("categories", created)
        count(
            "payment_accounts",
            connection.execute(
                text(
                    "INSERT INTO payment_accounts (organization_id, school_id, provider, "
                    "external_account_id, status, secret_ref, webhook_secret_hash) "
                    "VALUES (:o, :s, 'SANDBOX', :e, 'ACTIVE', 'env:APM_DEMO_PIX_SECRET', "
                    "encode(sha256(gen_random_uuid()::text::bytea), 'hex')) ON CONFLICT DO NOTHING"
                ),
                {"o": organization_id, "s": school_id, "e": f"demo-account-{slug}"},
            ).rowcount,
        )
        people = DEMO_PEOPLE.get(slug)
        has_movements: bool = connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM financial_transactions WHERE school_id = :s)"),
            {"s": school_id},
        ).scalar_one()
        if people is not None and not has_movements:
            written = _demo_movements(connection, school_id, organization_id, people)
            count("financial_transactions", written)


def _id(connection: Connection, sql: str, **params: Any) -> uuid.UUID:
    found: Any = connection.execute(text(sql), params).scalar_one()
    return uuid.UUID(str(found))


def _demo_movements(
    connection: Connection, school: uuid.UUID, org: uuid.UUID, people: tuple[str, str]
) -> int:
    treasurer, submitter = (_user(connection, email) for email in people)
    category = {
        key: _id(
            connection,
            "SELECT id FROM categories WHERE school_id = :s AND key = :k",
            s=school,
            k=key,
        )
        for key, *_ in DEFAULT_CATEGORIES
    }
    account = _id(
        connection,
        "SELECT id FROM payment_accounts WHERE school_id = :s AND status = 'ACTIVE'",
        s=school,
    )
    scope = {"o": org, "s": school}
    written = 0

    def transaction(**values: Any) -> uuid.UUID:
        nonlocal written
        written += 1
        return _id(
            connection,
            "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, "
            "amount_cents, status, category_id, origin_type, origin_user_id, occurred_at, "
            "settled_at, created_by_user_id, parent_transaction_id, parent_kind) "
            "VALUES (:o, :s, :kind, :direction, :amount, :status, :category, :origin_type, "
            ":origin_user, now() - make_interval(days => :days), "
            "CASE WHEN :settled THEN now() - make_interval(days => :days) END, "
            ":author, :parent, :parent_kind) "
            "RETURNING id",
            **{
                **scope,
                "origin_user": None,
                "author": None,
                "parent": None,
                "parent_kind": None,
                "days": 0,
                "settled": False,
                **values,
            },  # fmt: skip
        )

    def settle(tx: uuid.UUID, status: str, days: int) -> None:
        connection.execute(
            text(
                "UPDATE financial_transactions SET status = :st, "
                "settled_at = now() - make_interval(days => :d) WHERE id = :t"
            ),
            {"st": status, "d": days, "t": tx},
        )

    def move(tx: uuid.UUID, status: str) -> None:
        connection.execute(
            text("UPDATE financial_transactions SET status = :st WHERE id = :t"),
            {"st": status, "t": tx},
        )

    def contribution_manual(
        amount: int,
        days: int,
        *,
        method: str = "CASH",
        donation: bool = False,
        name: str | None = None,
    ) -> None:
        tx = transaction(
            kind="CONTRIBUTION", direction="IN", amount=amount, status="PAID",
            category=category["donation" if donation else "parent_contribution"],
            origin_type="OTHER" if donation else "GUARDIAN", days=days, author=treasurer,
            settled=True,
        )  # fmt: skip
        connection.execute(
            text(
                "INSERT INTO contributions (transaction_id, organization_id, school_id, method, "
                "guardian_name, student_name, class_name, contributor_email, contributor_phone) "
                "VALUES (:t, :o, :s, :m, :g, :st, :c, :e, :p)"
            ),
            {
                **scope, "t": tx, "m": method, "g": name,
                "st": "Aluno Demo" if name else None, "c": "3A" if name else None,
                "e": "responsavel.demo@example.test" if name else None,
                "p": "11 90000-0000" if name else None,
            },
        )  # fmt: skip

    def contribution_pix(amount: int, days: int, *, outcome: str) -> None:
        tx = transaction(
            kind="CONTRIBUTION", direction="IN", amount=amount, status="PENDING_PAYMENT",
            category=category["parent_contribution"], origin_type="GUARDIAN", days=days,
        )  # fmt: skip
        connection.execute(
            text(
                "INSERT INTO contributions (transaction_id, organization_id, school_id, method, "
                "receipt_token_hash, receipt_expires_at) "
                "VALUES (:t, :o, :s, 'PIX', encode(sha256(gen_random_uuid()::text::bytea), 'hex'), "
                "now() + interval '30 days')"
            ),
            {**scope, "t": tx},
        )
        charge = _id(
            connection,
            "INSERT INTO pix_charges (organization_id, school_id, transaction_id, payment_account_id, "
            "provider, txid, amount_cents, expires_at) VALUES (:o, :s, :t, :a, 'SANDBOX', "
            "replace(gen_random_uuid()::text, '-', ''), :amount, now() + interval '30 minutes') "
            "RETURNING id",
            **scope, t=tx, a=account, amount=amount,
        )  # fmt: skip
        if outcome == "pending":
            return
        received = amount if outcome == "paid" else amount + 500
        connection.execute(
            text(
                "UPDATE pix_charges SET status = :st, paid_at = now() - make_interval(days => :d), "
                "received_amount_cents = :r, divergence_reason = :why, end_to_end_id = 'E' || "
                "substr(replace(gen_random_uuid()::text, '-', ''), 1, 31) WHERE id = :c"
            ),
            {
                "st": "PAID" if outcome == "paid" else "REVIEW_REQUIRED", "d": days, "r": received,
                "why": None if outcome == "paid" else "valor recebido diferente do esperado (demo)",
                "c": charge,
            },
        )  # fmt: skip
        if outcome == "paid":
            settle(tx, "PAID", days)
        else:
            move(tx, "REVIEW_REQUIRED")

    def contribution_pix_direct(amount: int, days: int, *, name: str) -> None:
        """A Pix paid straight to the key of the APM, registered by the treasury (born PAID)."""
        tx = transaction(
            kind="CONTRIBUTION", direction="IN", amount=amount, status="PAID",
            category=category["parent_contribution"], origin_type="GUARDIAN", days=days,
            author=treasurer, settled=True,
        )  # fmt: skip
        connection.execute(
            text(
                "INSERT INTO contributions (transaction_id, organization_id, school_id, method, "
                "external_reference, guardian_name) VALUES (:t, :o, :s, 'PIX_DIRECT', "
                "'E' || substr(replace(gen_random_uuid()::text, '-', ''), 1, 31), :g)"
            ),
            {**scope, "t": tx, "g": name},
        )

    def bank_fee(amount: int, days: int, description: str) -> None:
        """A fee of the bank: an expense of the APM with no approver, born APPROVED and settled by
        whoever records it."""
        tx = transaction(
            kind="EXPENSE", direction="OUT", amount=amount, status="APPROVED",
            category=category["bank_fees"], origin_type="BANK", days=days, author=treasurer,
        )  # fmt: skip
        connection.execute(
            text(
                "INSERT INTO expenses (transaction_id, organization_id, school_id, description, "
                "paid_by, submitted_by_user_id) VALUES (:t, :o, :s, :d, 'APM', :u)"
            ),
            {**scope, "t": tx, "d": description, "u": treasurer},
        )
        settle(tx, "PAID", days)

    def expense(
        amount: int,
        days: int,
        status: str,
        *,
        paid_by: str = "APM",
        key: str = "school_supplies",
        approved: int | None = None,
    ) -> uuid.UUID:
        start = "DRAFT" if status == "DRAFT" else "SUBMITTED"
        tx = transaction(
            kind="EXPENSE", direction="OUT", amount=amount, status=start, category=category[key],
            origin_type="TEACHER", origin_user=submitter, days=days, author=submitter,
        )  # fmt: skip
        connection.execute(
            text(
                "INSERT INTO expenses (transaction_id, organization_id, school_id, description, "
                "vendor, purchase_reason, payment_method, paid_by, submitted_by_user_id) "
                "VALUES (:t, :o, :s, :d, 'Fornecedor Demo', 'projeto da turma (demo)', 'CARD', :p, :u)"
            ),
            {**scope, "t": tx, "d": f"Despesa demonstrativa de R$ {amount / 100:.2f}", "p": paid_by, "u": submitter},
        )  # fmt: skip
        if start != "DRAFT":
            connection.execute(
                text(
                    "INSERT INTO expense_attachments (organization_id, school_id, transaction_id, "
                    "kind, storage_key, file_name, content_type, size_bytes, sha256, "
                    "uploaded_by_user_id) VALUES (:o, :s, :t, 'INVOICE', :k, 'nota-demo.pdf', "
                    "'application/pdf', 2048, encode(sha256(gen_random_uuid()::text::bytea), 'hex'), :u)"
                ),
                {**scope, "t": tx, "k": f"demo/{tx}", "u": submitter},
            )
        if status == "CORRECTION_REQUESTED":
            connection.execute(
                text(
                    "UPDATE expenses SET correction_reason = 'anexe o comprovante do pagamento (demo)' WHERE transaction_id = :t"
                ),
                {"t": tx},
            )
            move(tx, "CORRECTION_REQUESTED")
        elif status in ("APPROVED", "PAID"):
            connection.execute(
                text(
                    "UPDATE expenses SET approved_by_user_id = :u, approved_amount_cents = :a "
                    "WHERE transaction_id = :t"
                ),
                {"u": treasurer, "a": approved or amount, "t": tx},
            )
            move(tx, "APPROVED")
            if status == "PAID":
                settle(tx, "PAID", days)
        return tx

    def reimbursement(expense_id: uuid.UUID, amount: int, days: int, *, paid: bool) -> None:
        tx = transaction(
            kind="REIMBURSEMENT", direction="OUT", amount=amount, status="PENDING",
            category=category["teacher_reimbursement"], origin_type="TEACHER",
            origin_user=submitter, days=days, author=treasurer, parent=expense_id,
            parent_kind="EXPENSE",
        )  # fmt: skip
        connection.execute(
            text(
                "INSERT INTO reimbursements (transaction_id, organization_id, school_id, "
                "beneficiary_user_id) VALUES (:t, :o, :s, :b)"
            ),
            {**scope, "t": tx, "b": submitter},
        )
        if paid:
            connection.execute(
                text(
                    "UPDATE reimbursements SET payment_reference = 'demo-transferencia-001', "
                    "paid_by_user_id = :u WHERE transaction_id = :t"
                ),
                {"u": treasurer, "t": tx},
            )
            settle(tx, "PAID", days)
            settle(expense_id, "PAID", days)

    def give_back(
        amount: int, days: int, *, confirmed: bool, parent: uuid.UUID | None = None
    ) -> None:
        tx = transaction(
            kind="REFUND", direction="IN", amount=amount, status="REQUESTED",
            category=category["refund"], origin_type="TEACHER", origin_user=submitter, days=days,
            author=treasurer, parent=parent, parent_kind="EXPENSE" if parent else None,
        )  # fmt: skip
        connection.execute(
            text(
                "INSERT INTO refunds (transaction_id, organization_id, school_id, reason) "
                "VALUES (:t, :o, :s, 'Saldo não utilizado (demonstração)')"
            ),
            {**scope, "t": tx},
        )
        if confirmed:
            move(tx, "AWAITING_CONFIRMATION")
            connection.execute(
                text("UPDATE refunds SET confirmed_by_user_id = :u WHERE transaction_id = :t"),
                {"u": treasurer, "t": tx},
            )
            settle(tx, "CONFIRMED", days)

    contribution_manual(5000, 24, name="Responsável Demo 1")
    contribution_manual(2000, 19)
    contribution_manual(10000, 12, name="Responsável Demo 2")
    contribution_manual(30000, 9, method="TRANSFER", donation=True)
    contribution_pix(3000, 8, outcome="paid")
    contribution_pix(1500, 0, outcome="pending")
    contribution_pix(4000, 2, outcome="review")
    contribution_pix_direct(2000, 5, name="Responsável Demo 3")
    paid = expense(12000, 20, "PAID", key="services")
    expense(3500, 6, "SUBMITTED", key="other_authorized")
    expense(8000, 3, "APPROVED")
    expense(900, 1, "DRAFT")
    expense(1100, 4, "CORRECTION_REQUESTED")
    bank_fee(590, 7, "Tarifa Pix Enviado (demonstração)")
    reimbursement(expense(4500, 15, "APPROVED", paid_by="COLLABORATOR"), 4500, 10, paid=False)
    partial = expense(3000, 22, "APPROVED", paid_by="COLLABORATOR", approved=2200)
    reimbursement(partial, 2200, 21, paid=True)
    give_back(2500, 11, confirmed=True, parent=paid)
    give_back(700, 1, confirmed=False)
    return written
