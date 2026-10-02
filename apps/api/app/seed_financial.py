"""Financial part of the development seed. FAKE data only (the caller refuses ENV=production).

Two things:
  * the default categories of every school (keys in English, names in Portuguese), which a school
    created later gets from the service that creates it (DEFAULT_CATEGORIES is the list to use);
  * demonstration movements for each demo school that has none yet: contributions in cash and by
    Pix, expenses in every state, a reimbursement, a refund. They are written the way the
    application will write them, so every trigger of the schema runs (reference codes, audit,
    consistency at commit), and the actor of the audit records is SYSTEM.

It runs as the admin of the development database (a superuser, so row level security does not
get in the way) and is safe to run twice.
"""

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import Connection, text

# (key, name, applies to). Keys are stable identifiers; names are what the school sees.
DEFAULT_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("donation", "Doação", "IN"),
    ("event_income", "Receita de evento", "IN"),
    ("other_income", "Outras receitas", "IN"),
    ("supplies", "Material", "OUT"),
    ("maintenance", "Manutenção", "OUT"),
    ("events", "Eventos", "OUT"),
    ("equipment", "Equipamentos", "OUT"),
    ("food", "Alimentação", "OUT"),
    ("other_expense", "Outras despesas", "OUT"),
)

# school slug -> (who enters cash and decides, who submits expenses). Two different people, both
# active members whose membership covers the school (the schema checks it).
DEMO_PEOPLE = {
    "demo-aurora": ("carla.tesoureira@example.test", "bruno.diretor@example.test"),
    "demo-horizonte": ("ana.admin@example.test", "diego.professor@example.test"),
    "demo-central": ("fabio.admin@example.test", "gina.tesoureira@example.test"),
}

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
        for key, name, applies_to in DEFAULT_CATEGORIES:
            created += connection.execute(
                text(
                    "INSERT INTO categories (organization_id, school_id, key, name, applies_to) "
                    "VALUES (:o, :s, :k, :n, :a) ON CONFLICT DO NOTHING"
                ),
                {"o": organization_id, "s": school_id, "k": key, "n": name, "a": applies_to},
            ).rowcount
        count("categories", created)
        people = DEMO_PEOPLE.get(slug)
        has_movements: bool = connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM financial_transactions WHERE school_id = :s)"),
            {"s": school_id},
        ).scalar_one()
        if people is not None and not has_movements:
            count(
                "financial_transactions",
                _demo_movements(connection, school_id, organization_id, people),
            )


def _category(connection: Connection, school_id: uuid.UUID, key: str) -> uuid.UUID:
    found: Any = connection.execute(
        text("SELECT id FROM categories WHERE school_id = :s AND key = :k"),
        {"s": school_id, "k": key},
    ).scalar_one()
    return uuid.UUID(str(found))


def _demo_movements(
    connection: Connection, school: uuid.UUID, org: uuid.UUID, people: tuple[str, str]
) -> int:
    treasurer, submitter = (_user(connection, email) for email in people)
    donation = _category(connection, school, "donation")
    supplies = _category(connection, school, "supplies")
    events = _category(connection, school, "events")
    scope = {"o": org, "s": school}
    written = 0

    def transaction(**values: Any) -> uuid.UUID:
        nonlocal written
        written += 1
        row: Any = connection.execute(
            text(
                "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, "
                "amount_cents, status, category_id, occurred_at, settled_at, "
                "parent_transaction_id, parent_kind, created_by_user_id) "
                "VALUES (:o, :s, :kind, :direction, :amount, "
                ":status, :category, now() - make_interval(days => :days), "
                "CASE WHEN :settled THEN now() - make_interval(days => :days) END, :parent, "
                ":parent_kind, :author) RETURNING id"
            ),
            {
                **scope, "category": None, "parent": None, "parent_kind": None, "author": None,
                "settled": False, "days": 0, **values,
            },
        ).scalar_one()  # fmt: skip
        return uuid.UUID(str(row))

    def contribution_cash(amount: int, days: int, guardian: str | None = None) -> uuid.UUID:
        tx = transaction(
            kind="CONTRIBUTION", direction="IN", amount=amount, status="PAID", category=donation,
            days=days, settled=True, author=treasurer,
        )  # fmt: skip
        connection.execute(
            text(
                "INSERT INTO contributions (transaction_id, organization_id, school_id, method, "
                "guardian_name, student_name, class_name) "
                "VALUES (:t, :o, :s, 'CASH', :g, :st, :c)"
            ),
            {
                **scope,
                "t": tx,
                "g": guardian,
                "st": "Aluno Demo" if guardian else None,
                "c": "3A" if guardian else None,
            },
        )
        return tx

    def contribution_pix(amount: int, days: int, *, paid: bool) -> uuid.UUID:
        tx = transaction(
            kind="CONTRIBUTION", direction="IN", amount=amount, status="PENDING_PAYMENT",
            category=donation, days=days,
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
        charge: Any = connection.execute(
            text(
                "INSERT INTO pix_charges (organization_id, school_id, transaction_id, provider, "
                "txid, amount_cents, expires_at) VALUES (:o, :s, :t, 'SANDBOX', "
                "replace(gen_random_uuid()::text, '-', ''), :a, now() + interval '30 minutes') "
                "RETURNING id"
            ),
            {**scope, "t": tx, "a": amount},
        ).scalar_one()
        if paid:
            connection.execute(
                text(
                    "UPDATE pix_charges SET status = 'PAID', "
                    "paid_at = now() - make_interval(days => :d), "
                    "end_to_end_id = 'E' || "
                    "substr(replace(gen_random_uuid()::text, '-', ''), 1, 31) WHERE id = :c"
                ),
                {"d": days, "c": charge},
            )
            connection.execute(
                text(
                    "UPDATE financial_transactions SET status = 'PAID', "
                    "settled_at = now() - make_interval(days => :d) WHERE id = :t"
                ),
                {"d": days, "t": tx},
            )
        return tx

    def expense(
        amount: int, days: int, status: str, *, paid_by: str = "APM", category: uuid.UUID = supplies
    ) -> uuid.UUID:
        decided = status in ("APPROVED", "PAID")
        tx = transaction(
            kind="EXPENSE", direction="OUT", amount=amount, status=status, category=category,
            days=days, settled=status == "PAID", author=submitter,
        )  # fmt: skip
        connection.execute(
            text(
                "INSERT INTO expenses (transaction_id, organization_id, school_id, description, "
                "vendor, paid_by, submitted_by_user_id, approved_by_user_id, decision_reason) "
                "VALUES (:t, :o, :s, :d, 'Fornecedor Demo', :p, :u, :a, :r)"
            ),
            {
                **scope,
                "t": tx,
                "d": f"Despesa demonstrativa de R$ {amount / 100:.2f}",
                "p": paid_by,
                "u": submitter,
                "a": treasurer if decided else None,
                "r": None,
            },
        )  # fmt: skip
        return tx

    def reimbursement(expense_id: uuid.UUID, amount: int, days: int, *, paid: bool) -> uuid.UUID:
        tx = transaction(
            kind="REIMBURSEMENT", direction="OUT", amount=amount,
            status="PAID" if paid else "PENDING", days=days, settled=paid, author=treasurer,
            parent=expense_id, parent_kind="EXPENSE",
        )  # fmt: skip
        connection.execute(
            text(
                "INSERT INTO reimbursements (transaction_id, organization_id, school_id, "
                "beneficiary_user_id, payment_reference) VALUES (:t, :o, :s, :b, :r)"
            ),
            {**scope, "t": tx, "b": submitter, "r": "demo-transferencia-001" if paid else None},
        )
        return tx

    def refund(parent: uuid.UUID, amount: int) -> uuid.UUID:
        tx = transaction(
            kind="REFUND", direction="OUT", amount=amount, status="PENDING", author=treasurer,
            parent=parent, parent_kind="CONTRIBUTION",
        )  # fmt: skip
        connection.execute(
            text(
                "INSERT INTO refunds (transaction_id, organization_id, school_id, reason) "
                "VALUES (:t, :o, :s, 'Pagamento em duplicidade (demonstração)')"
            ),
            {**scope, "t": tx},
        )
        return tx

    contribution_cash(5000, 24, guardian="Responsável Demo 1")
    contribution_cash(2000, 19)
    first = contribution_cash(10000, 12, guardian="Responsável Demo 2")
    contribution_pix(3000, 8, paid=True)
    contribution_pix(1500, 0, paid=False)
    expense(12000, 20, "PAID")
    expense(3500, 6, "SUBMITTED", category=events)
    expense(8000, 3, "APPROVED")
    reimbursement(expense(4500, 15, "APPROVED", paid_by="COLLABORATOR"), 4500, 10, paid=False)
    paid_back = expense(2200, 22, "APPROVED", paid_by="COLLABORATOR")
    reimbursement(paid_back, 2200, 21, paid=True)
    connection.execute(
        text(
            "UPDATE financial_transactions SET status = 'PAID', "
            "settled_at = now() - interval '21 days' WHERE id = :t"
        ),
        {"t": paid_back},
    )
    refund(first, 2500)
    return written
