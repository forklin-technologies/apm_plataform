"""The pieces of the PDF that need no database: how things are written, and the reconciliation of
the sections with the snapshot of a closing."""

from datetime import UTC, date, datetime
from typing import Any

import pytest

from app.reports.closing_report import (
    ReportMismatch,
    build_closing_report,
    render_closing_report,
)
from app.reports.format import (
    brl,
    initials,
    local_datetime,
    month_name,
    percent,
    printable,
    reference,
    short_id,
)
from tests.statement.pdftext import pages_of, text_of


@pytest.mark.parametrize(
    ("cents", "written"),
    [
        (0, "R$ 0,00"),
        (5, "R$ 0,05"),
        (100, "R$ 1,00"),
        (123456, "R$ 1.234,56"),
        (123456789, "R$ 1.234.567,89"),
        (100000000000, "R$ 1.000.000.000,00"),
        (-150, "-R$ 1,50"),
        (-123456, "-R$ 1.234,56"),
    ],
)
def test_money_is_written_the_brazilian_way_from_integer_cents(cents: int, written: str) -> None:
    assert brl(cents) == written


def test_the_small_writers() -> None:
    assert reference(42) == "APM-000042"
    assert reference(1234567) == "APM-1234567"
    assert initials("Maria Exemplo") == "M. E."
    assert initials("  joão  da  silva ") == "J. D. S."
    assert initials(None) == "—" and initials("") == "—"
    assert percent(1, 3) == "33,3%" and percent(2, 3) == "66,7%" and percent(1, 1) == "100,0%"
    assert percent(0, 0) == "—"
    assert short_id("E1234567890123456789012345678901") == "E12345678901…"
    assert short_id(None) == "—" and short_id("E123") == "E123"
    assert month_name(date(2026, 3, 1)) == "março de 2026"


def test_an_instant_is_written_in_the_time_zone_of_the_school() -> None:
    moment = datetime(2025, 3, 5, 2, 30, tzinfo=UTC)

    assert local_datetime(moment, "America/Sao_Paulo") == "04/03/2025 23:30"
    assert local_datetime(moment, "UTC") == "05/03/2025 02:30"


def test_text_the_standard_fonts_cannot_draw_degrades_instead_of_failing() -> None:
    assert printable("Ação São João") == "Ação São João"
    assert printable("Łukasz") == "Lukasz"
    assert printable("名前") == "??"


# --- the snapshot and its sections ---------------------------------------------------------------

AT = datetime(2025, 3, 10, 15, 0, tzinfo=UTC)


def entry(code: int, kind: str, group: str, amount: int, **extra: Any) -> dict[str, Any]:
    return {
        "reference_code": code,
        "kind": kind,
        "report_group": group,
        "settled_at": AT,
        "origin_label": "Maria Exemplo",
        "beneficiary_label": None,
        "origin_type": "GUARDIAN",
        "description": "Descrição",
        "category_key": "cat",
        "status_label": "PAID",
        "amount_cents": amount,
        "late_adjustment": False,
        **extra,
    }


def snapshot(**overrides: Any) -> dict[str, Any]:
    closing: dict[str, Any] = {
        "id": "0",
        "timezone": "America/Sao_Paulo",
        "period_start": date(2025, 3, 1),
        "period_end": date(2025, 3, 31),
        "opening_balance_cents": 1000,
        "contributions_in_cents": 500,
        "other_in_cents": 300,
        "refunds_in_cents": 50,
        "total_in_cents": 850,
        "expenses_out_cents": 235,
        "reimbursements_out_cents": 400,
        "total_out_cents": 635,
        "closing_balance_cents": 1215,
        "pending_reimbursements_cents": 150,
        "closing_after_pending_cents": 1065,
        "entries_count": 6,
        "entries_hash": "ab" * 32,
        "bank_balance_reported_cents": None,
        "bank_difference_cents": None,
        "closed_at": AT,
        "breakdown": {},
        **overrides,
    }
    return closing


def ledger() -> list[dict[str, Any]]:
    return [
        entry(1, "CONTRIBUTION", "CONTRIBUTIONS", 500),
        entry(2, "CONTRIBUTION", "OTHER_INCOME", 300, origin_label=None),
        entry(3, "EXPENSE", "EXPENSES_REIMBURSEMENTS", 200, origin_type="TEACHER"),
        entry(
            4, "REIMBURSEMENT", "EXPENSES_REIMBURSEMENTS", 400, beneficiary_label="Pedro Professor",
            status_label="REIMBURSED", origin_type="TEACHER",
        ),
        entry(5, "EXPENSE", "BANK_FEES", 35, origin_label="Banco", origin_type="BANK"),
        entry(6, "REFUND", "REFUNDS", 50, status_label="CONFIRMED", origin_type="TEACHER"),
    ]  # fmt: skip


def build(**changes: Any) -> Any:
    arguments: dict[str, Any] = {
        "closing": snapshot(),
        "entries": ledger(),
        "pending": [],
        "category_names": {"cat": "Categoria"},
        "organization_name": "APM Teste",
        "school_name": "Escola Teste",
        "generated_at": datetime(2025, 4, 1, 12, 0, tzinfo=UTC),
        "personal_data": True,
        **changes,
    }
    return build_closing_report(**arguments)


def test_the_sections_add_up_to_the_columns_of_the_closing() -> None:
    report = build()

    assert [line.amount_cents for line in report.incoming] == [500, 300]
    assert [line.amount_cents for line in report.outgoing] == [200, 400]
    assert [line.amount_cents for line in report.refunds] == [50]
    assert [line.amount_cents for line in report.fees] == [35]
    assert report.incoming[1].who == "Contribuinte anônimo"
    assert report.outgoing[1].who == "Pedro Professor"  # a reimbursement shows its beneficiary


@pytest.mark.parametrize(
    "tamper",
    [
        {"contributions_in_cents": 501, "total_in_cents": 851, "closing_balance_cents": 1216},
        {"other_in_cents": 299, "total_in_cents": 849, "closing_balance_cents": 1214},
        {"refunds_in_cents": 51, "total_in_cents": 851, "closing_balance_cents": 1216},
        {"expenses_out_cents": 236, "total_out_cents": 636, "closing_balance_cents": 1214},
        {"reimbursements_out_cents": 401, "total_out_cents": 636, "closing_balance_cents": 1214},
        {"entries_count": 7},
        {"total_in_cents": 851},
        {"closing_balance_cents": 1216},
    ],
    ids=lambda value: ",".join(value),
)
def test_a_closing_the_ledger_does_not_reproduce_gets_no_pdf(tamper: dict[str, int]) -> None:
    with pytest.raises(ReportMismatch):
        build(closing=snapshot(**tamper))


def test_an_entry_that_is_missing_or_extra_is_caught() -> None:
    with pytest.raises(ReportMismatch):
        build(entries=ledger()[:-1])
    with pytest.raises(ReportMismatch):
        build(entries=[*ledger(), entry(7, "CONTRIBUTION", "CONTRIBUTIONS", 1)])


def test_without_the_right_to_see_people_the_names_are_initials() -> None:
    report = build(personal_data=False)

    assert report.incoming[0].who == "M. E."
    assert report.incoming[1].who == "Contribuinte anônimo"
    assert report.outgoing[1].who == "P. P."
    text = text_of(render_closing_report(report))
    assert "Maria" not in text and "Exemplo" not in text and "Pedro" not in text
    assert "M. E." in text and "Versão sem dados pessoais" in text


def test_the_pdf_is_the_same_bytes_for_the_same_data() -> None:
    assert render_closing_report(build()) == render_closing_report(build())


def test_odd_text_in_a_name_or_description_does_not_break_the_page() -> None:
    odd = ledger()
    odd[0] = entry(
        1, "CONTRIBUTION", "CONTRIBUTIONS", 500, origin_label='<b>Łukasz</b> & Filhos "X"'
    )
    odd[2] = entry(3, "EXPENSE", "EXPENSES_REIMBURSEMENTS", 200, description="Tinta <script>&amp;")

    content = render_closing_report(build(entries=odd))

    assert content.startswith(b"%PDF-")
    assert "Lukasz" in text_of(content)


def test_a_large_period_runs_over_many_pages_with_the_page_count_and_the_code_on_each() -> None:
    entries = [entry(n, "CONTRIBUTION", "CONTRIBUTIONS", 100) for n in range(1, 301)]
    closing = snapshot(
        contributions_in_cents=30000, other_in_cents=0, refunds_in_cents=0, total_in_cents=30000,
        expenses_out_cents=0, reimbursements_out_cents=0, total_out_cents=0,
        closing_balance_cents=31000, entries_count=300,
    )  # fmt: skip

    pages = pages_of(render_closing_report(build(closing=closing, entries=entries)))

    assert len(pages) >= 8
    for number, page in enumerate(pages, start=1):
        assert f"Página {number} de {len(pages)}" in page
        assert "ab" * 32 in page
    assert "Tesoureiro" in pages[-1]
    assert "Subtotal de entradas (300) R$ 300,00" in " ".join(pages)
