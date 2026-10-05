# ruff: noqa: E501  (SQL text)
"""F13: the examples of the product document, worked out by hand (amounts in cents).

Section 10 (the dashboard of the management) and section 11 (the statement) are rebuilt as ledgers
and every number of the document is checked against what the database computes. Section 12 (the
monthly closing) is checked with its breakdown by report group and category. The document's dates
are in 2026; the examples here use October 2025 because a settlement cannot be dated in the future.
"""

import uuid
from datetime import date

from sqlalchemy import Connection, text

from tests.financial.support import (
    Fresh,
    add_cash_contribution,
    add_collaborator_expense,
    add_refund,
    entries,
    summary_row,
    utc,
)

OCT = (date(2025, 10, 1), date(2025, 10, 31))


def _dashboard_ledger(conn: Connection, f: Fresh, *, other_income: int = 0) -> None:
    """Section 10: entradas 4.850,00; reembolsos realizados 1.900,00; pendentes 450,00; devoluções 120,00."""
    add_cash_contribution(conn, f, 200000, utc(2025, 10, 3, 15))
    add_cash_contribution(conn, f, 150000, utc(2025, 10, 10, 15))
    add_cash_contribution(conn, f, 135000, utc(2025, 10, 17, 15))
    if other_income:
        add_cash_contribution(
            conn,
            f,
            other_income,
            utc(2025, 10, 20, 15),
            method="TRANSFER",
            category=f.cat_other,
            origin_type="OTHER",
        )
    for amount, day in ((90000, 6), (60000, 13), (40000, 20)):  # three reimbursements already paid
        add_collaborator_expense(conn, f, amount, reimbursed_at=utc(2025, 10, day, 15))
    add_collaborator_expense(conn, f, 45000, occurred_at=utc(2025, 10, 25, 15))  # a pending one
    add_refund(
        conn,
        f,
        12000,
        status="CONFIRMED",
        settled_at=utc(2025, 10, 22, 15),
        reason="saldo não utilizado",
    )


def test_the_dashboard_of_the_document(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    _dashboard_ledger(conn, f)

    s = summary_row(conn, f, *OCT)

    # The five indicators of section 10:
    assert s.contributions_in_cents == 485000  # Entradas R$ 4.850,00
    assert s.reimbursements_out_cents == 190000  # Reembolsos realizados R$ 1.900,00
    assert s.pending_reimbursements_cents == 45000  # Reembolsos pendentes R$ 450,00
    assert s.refunds_in_cents == 12000  # Devoluções R$ 120,00
    assert (
        s.reimbursements_out_cents + s.pending_reimbursements_cents == 235000
    )  # Despesas R$ 2.350,00
    # The document's Saldo: entradas - despesas = R$ 2.500,00 (it leaves the devoluções out).
    assert (
        s.contributions_in_cents - (s.reimbursements_out_cents + s.pending_reimbursements_cents)
        == 250000
    )
    # What the database offers instead, the two balances (the open question of the product-spec note):
    assert s.closing_balance_cents == 307000  # em caixa: 4.850 + 120 - 1.900
    assert (
        s.balance_after_pending_cents == 262000
    )  # após reembolsos pendentes: 3.070 - 450 = 2.500 + 120
    assert s.balance_after_pending_cents == 250000 + s.refunds_in_cents
    # And the totals the figures are made of.
    assert (
        s.opening_balance_cents,
        s.other_in_cents,
        s.total_in_cents,
        s.expenses_out_cents,
        s.total_out_cents,
        s.entries_count,
    ) == (0, 0, 497000, 0, 190000, 7)


def test_the_dashboard_filtered_by_type_category_and_status(
    world: tuple[Connection, Fresh],
) -> None:
    conn, f = world
    _dashboard_ledger(conn, f)

    incomes = entries(conn, f, *OCT, p_type="INCOME")
    expenses = entries(conn, f, *OCT, p_type="EXPENSE")
    refunds = entries(conn, f, *OCT, p_type="REFUND")
    by_category = entries(conn, f, *OCT, p_category=f.cat_reimb)
    reimbursed = entries(conn, f, *OCT, p_status="REIMBURSED")

    assert [r.signed_amount_cents for r in incomes] == [200000, 150000, 135000]
    assert [r.signed_amount_cents for r in expenses] == [-90000, -60000, -40000]
    assert [r.signed_amount_cents for r in refunds] == [12000]
    assert len(by_category) == 3 and len(reimbursed) == 3
    # The running balance does not change with the filter: it is the balance of the account.
    full = {r.transaction_id: r.running_balance_cents for r in entries(conn, f, *OCT)}
    assert all(
        full[r.transaction_id] == r.running_balance_cents for r in incomes + expenses + refunds
    )


def test_the_statement_of_the_document(world: tuple[Connection, Fresh]) -> None:
    """Section 11: Entrada João 30 (Pago), Despesa Maria 87,50 (Reembolsado), Entrada Ana 40 (Pago),
    Devolução Maria 20 (Confirmado)."""
    conn, f = world
    conn.execute(text("UPDATE users SET full_name = 'Maria' WHERE id = :u"), {"u": f.staff})
    conn.execute(
        text("UPDATE categories SET name = 'Contribuição APM' WHERE id = :c"), {"c": f.cat_in}
    )
    add_cash_contribution(conn, f, 3000, utc(2025, 10, 1, 15), guardian="João")
    add_collaborator_expense(
        conn, f, 8750, reimbursed_at=utc(2025, 10, 2, 15), description="Material escolar"
    )
    add_cash_contribution(conn, f, 4000, utc(2025, 10, 3, 15), guardian="Ana")
    add_refund(
        conn,
        f,
        2000,
        status="CONFIRMED",
        settled_at=utc(2025, 10, 5, 15),
        reason="Saldo não utilizado",
        origin_user_id=f.staff,
    )

    rows = entries(conn, f, *OCT)

    # (Tipo, Origem, Descrição, Entrada, Saída, Status), date and running balance
    assert [
        (r.local_date.day, r.display_type, r.origin_label, r.description,
         r.signed_amount_cents if r.signed_amount_cents > 0 else None,
         -r.signed_amount_cents if r.signed_amount_cents < 0 else None,
         r.status_label, r.running_balance_cents)
        for r in rows
    ] == [
        (1, "INCOME", "João", "Contribuição APM", 3000, None, "PAID", 3000),
        (2, "EXPENSE", "Maria", "Material escolar", None, 8750, "REIMBURSED", -5750),
        (3, "INCOME", "Ana", "Contribuição APM", 4000, None, "PAID", -1750),
        (5, "REFUND", "Maria", "Saldo não utilizado", 2000, None, "CONFIRMED", 250),
    ]  # fmt: skip
    # The reimbursement line carries the beneficiary, and the expense is not a second line.
    assert [r.beneficiary_label for r in rows] == [None, "Maria", None, None]
    assert [r.kind for r in rows] == ["CONTRIBUTION", "REIMBURSEMENT", "CONTRIBUTION", "REFUND"]
    s = summary_row(conn, f, *OCT)
    assert (s.total_in_cents, s.total_out_cents, s.closing_balance_cents) == (9000, 8750, 250)


def test_the_statement_filtered_by_person(world: tuple[Connection, Fresh]) -> None:
    conn, f = world
    conn.execute(text("UPDATE users SET full_name = 'Maria' WHERE id = :u"), {"u": f.staff})
    add_cash_contribution(conn, f, 3000, utc(2025, 10, 1, 15), guardian="João")
    add_collaborator_expense(
        conn, f, 8750, reimbursed_at=utc(2025, 10, 2, 15), description="Material escolar"
    )
    add_refund(
        conn, f, 2000, status="CONFIRMED", settled_at=utc(2025, 10, 5, 15), origin_user_id=f.staff
    )

    marias = entries(conn, f, *OCT, p_person=f.staff)  # her reimbursement and her devolução
    nobody = entries(conn, f, *OCT, p_person=f.outsider)

    assert [(r.display_type, r.signed_amount_cents) for r in marias] == [
        ("EXPENSE", -8750),
        ("REFUND", 2000),
    ]
    assert nobody == []


def test_the_monthly_closing_of_the_document_with_its_breakdown(
    world: tuple[Connection, Fresh],
) -> None:
    """Section 12, Outubro: contribuições 4.850,00; outras entradas 500,00; despesas/reembolsos; devoluções 120,00."""
    conn, f = world
    _dashboard_ledger(conn, f, other_income=50000)

    closing: uuid.UUID = conn.execute(
        text(
            "INSERT INTO monthly_closings (organization_id, school_id, period_start, closed_by_user_id) VALUES (:o, :s, '2025-10-01', :u) RETURNING id"
        ),
        {"o": f.org, "s": f.school, "u": f.treasurer},
    ).scalar_one()
    row = conn.execute(text("SELECT * FROM monthly_closings WHERE id = :c"), {"c": closing}).one()

    assert (
        row.contributions_in_cents,
        row.other_in_cents,
        row.refunds_in_cents,
        row.total_in_cents,
    ) == (485000, 50000, 12000, 547000)
    assert (row.expenses_out_cents, row.reimbursements_out_cents, row.total_out_cents) == (
        0,
        190000,
        190000,
    )
    assert (row.opening_balance_cents, row.closing_balance_cents, row.entries_count) == (
        0,
        357000,
        8,
    )
    assert (row.pending_reimbursements_cents, row.closing_after_pending_cents) == (
        45000,
        312000,
    )  # the photograph
    assert row.breakdown == {
        "CONTRIBUTIONS": {
            "in": 485000,
            "out": 0,
            "count": 3,
            "categories": {"parent_contribution": {"in": 485000, "out": 0, "count": 3}},
        },
        "OTHER_INCOME": {
            "in": 50000,
            "out": 0,
            "count": 1,
            "categories": {"donation": {"in": 50000, "out": 0, "count": 1}},
        },
        "EXPENSES_REIMBURSEMENTS": {
            "in": 0,
            "out": 190000,
            "count": 3,
            "categories": {"teacher_reimbursement": {"in": 0, "out": 190000, "count": 3}},
        },
        "REFUNDS": {
            "in": 12000,
            "out": 0,
            "count": 1,
            "categories": {"refund": {"in": 12000, "out": 0, "count": 1}},
        },
    }
    assert conn.execute(text("SELECT verify_closing(:c)"), {"c": closing}).scalar_one() is True
    # The breakdown is part of the verification: a change of it is detected.
    conn.execute(text("SET LOCAL session_replication_role = replica"))
    conn.execute(
        text("UPDATE monthly_closings SET breakdown = '{}'::jsonb WHERE id = :c"), {"c": closing}
    )
    conn.execute(text("RESET session_replication_role"))
    assert conn.execute(text("SELECT verify_closing(:c)"), {"c": closing}).scalar_one() is False


def test_the_pending_figure_of_a_closing_is_not_part_of_what_is_verified(
    world: tuple[Connection, Fresh],
) -> None:
    """The reimbursement that was pending is paid later: the closing still verifies (the hash covers the cash)."""
    conn, f = world
    _dashboard_ledger(conn, f)
    closing: uuid.UUID = conn.execute(
        text(
            "INSERT INTO monthly_closings (organization_id, school_id, period_start, closed_by_user_id) VALUES (:o, :s, '2025-10-01', :u) RETURNING id"
        ),
        {"o": f.org, "s": f.school, "u": f.treasurer},
    ).scalar_one()
    pending_at_closing: int = conn.execute(
        text("SELECT pending_reimbursements_cents FROM monthly_closings WHERE id = :c"),
        {"c": closing},
    ).scalar_one()

    # Pay the pending reimbursement now (a late adjustment, booked in the open month).
    conn.execute(
        text(
            "UPDATE reimbursements SET payment_reference = 'late', paid_by_user_id = :u WHERE transaction_id IN (SELECT id FROM financial_transactions WHERE school_id = :s AND status = 'PENDING' AND kind = 'REIMBURSEMENT')"
        ),
        {"u": f.treasurer, "s": f.school},
    )
    conn.execute(
        text(
            "UPDATE financial_transactions SET status = 'PAID', settled_at = :at WHERE school_id = :s AND status = 'PENDING' AND kind = 'REIMBURSEMENT'"
        ),
        {"at": utc(2025, 10, 28, 15), "s": f.school},
    )

    assert pending_at_closing == 45000
    assert conn.execute(text("SELECT verify_closing(:c)"), {"c": closing}).scalar_one() is True
    assert (
        conn.execute(
            text("SELECT pending_reimbursements_cents FROM monthly_closings WHERE id = :c"),
            {"c": closing},
        ).scalar_one()
        == 45000
    )
