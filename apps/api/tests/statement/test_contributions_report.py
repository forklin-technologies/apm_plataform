# ruff: noqa: E501  (SQL text)
"""The PDF of the contributions of a month: the sections add up to the summary of the database, what
is not in the cash ledger stays outside the total, the `viewer` gets initials."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, text

from tests.statement.conftest import LATE_AMOUNT, OUTSIDE_NOW, Login, Scene
from tests.statement.pdftext import pages_of, text_of


def contributions_path(school: Any, period: str | None = "2025-03") -> str:
    base = f"/api/v1/schools/{school}/reports/contributions.pdf"
    return base if period is None else f"{base}?period={period}"


def march_text(scene: Scene, login: Login, role: str = "treasurer") -> str:
    response = login(scene.users[role]).get(contributions_path(scene.fresh.school))
    assert response.status_code == 200, response.text
    return text_of(response.content)


def test_the_pdf_of_march_has_the_header_the_summary_and_the_lists(
    contrib_scene: Scene, login: Login
) -> None:
    scene = contrib_scene
    api = login(scene.users["treasurer"])

    response = api.get(contributions_path(scene.fresh.school))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="contribuicoes-2025-03.pdf"'
    )
    assert response.headers["cache-control"] == "private, no-store"
    assert response.content.startswith(b"%PDF-")
    pages = pages_of(response.content)
    everything = " ".join(pages)
    first = pages[0]
    assert "Relatório de contribuições dos responsáveis" in first
    assert "Org T5" in first and "School T5" in first and "março de 2025" in first
    assert "Gerado em" in first
    # the summary
    for expected in (
        "Quantidade de contribuições 6",
        "Total arrecadado R$ 155,00",
        "Menor contribuição R$ 15,00",
        "Maior contribuição R$ 40,00",
        "Valor médio R$ 25,83",  # 15500 / 6, to the cent
        # by amount: quantity, total, share
        "R$ 20,00 2 R$ 40,00 25,8%",
        "R$ 30,00 2 R$ 60,00 38,7%",
        "R$ 15,00 1 R$ 15,00 9,7%",
        "R$ 40,00 1 R$ 40,00 25,8%",
        # by channel
        "Pix pela plataforma 1 R$ 20,00 12,9%",
        "Pix direto na conta 1 R$ 30,00 19,4%",
        "Dinheiro 3 R$ 75,00 48,4%",
        "Transferência 1 R$ 30,00 19,4%",
    ):
        assert expected in first, expected
    # the lists: sections with their subtotals and counts
    assert "A. Contribuições dos responsáveis" in everything
    assert "Subtotal (6) R$ 155,00" in everything
    assert "B. Outras entradas" in everything and "Subtotal (1) R$ 100,00" in everything
    assert "C. Fora do total" in everything and "Nenhuma contribuição pendente" in everything
    for line in (
        "Maria Exemplo",
        "João Teste",
        "Carla Teste",
        "Pedro Quatro",
        "Ana Quinze",
        "Doador Generoso",
    ):
        assert line in everything, line
    assert "Aluno Um | 5º A (Profa Marta)" in everything
    assert "Contribuinte anônimo" in everything  # the Pix of the platform came with no name
    for channel in ("Pix pela plataforma", "Pix direto na conta", "Dinheiro", "Transferência"):
        assert channel in everything
    for outside in ("Ana Antiga", "Beatriz Abril", "Pessoa Alheia"):  # other months, other schools
        assert outside not in everything, outside
    # the reference and the shortened Pix id
    assert "APM-" in everything and "…" in everything
    for number, page in enumerate(pages, start=1):
        assert f"Página {number} de {len(pages)}" in page


def test_the_pix_ids_are_the_shortened_ones_of_the_charge_and_of_the_direct_pix(
    contrib_scene: Scene, login: Login, admin_engine: Engine
) -> None:
    scene = contrib_scene
    with admin_engine.connect() as conn:
        charge: str = conn.execute(
            text("SELECT end_to_end_id FROM pix_charges WHERE transaction_id = :t"),
            {"t": scene.entries["pix"]},
        ).scalar_one()
        direct: str = conn.execute(
            text("SELECT external_reference FROM contributions WHERE transaction_id = :t"),
            {"t": scene.entries["direct"]},
        ).scalar_one()

    everything = march_text(scene, login)

    assert f"{charge[:12]}…" in everything
    assert f"{direct[:12]}…" in everything
    assert charge not in everything and direct not in everything  # only the short form


def test_the_totals_are_the_ones_of_statement_summary(contrib_scene: Scene, login: Login) -> None:
    scene = contrib_scene
    api = login(scene.users["treasurer"])
    summary = api.get(
        f"/api/v1/schools/{scene.fresh.school}/statement/summary", params={"period": "2025-03"}
    ).json()

    everything = march_text(scene, login)

    assert summary["contributions_in_cents"] == 15500 and summary["other_in_cents"] == 10000
    assert "Subtotal (6) R$ 155,00" in everything  # section A = contributions_in_cents
    assert "Subtotal (1) R$ 100,00" in everything  # section B = other_in_cents
    assert "Total de entradas por contribuição (A + B) R$ 255,00" in everything


def test_what_is_not_in_the_cash_ledger_is_outside_the_total_and_a_late_adjustment_is_marked(
    contrib_scene: Scene, login: Login
) -> None:
    scene = contrib_scene
    now = datetime.now(ZoneInfo("America/Sao_Paulo"))
    api = login(scene.users["treasurer"])
    period = f"{now.year:04d}-{now.month:02d}"

    response = api.get(contributions_path(scene.fresh.school, period))
    summary = api.get(
        f"/api/v1/schools/{scene.fresh.school}/statement/summary", params={"period": period}
    ).json()

    assert response.status_code == 200, response.text
    everything = text_of(response.content)
    # section A holds only the late adjustment (booked now, flagged), and it is the summary's total
    assert summary["contributions_in_cents"] == LATE_AMOUNT
    assert "Subtotal (1) R$ 7,00" in everything
    assert "Tardia Silva" in everything and "(ajuste tardio)" in everything
    # the others are listed apart, with their own status, and never in a total
    assert "C. Fora do total" in everything
    for status in ("Aguardando pagamento", "Em análise", "Cancelada"):
        assert status in everything, status
    assert (
        f"Valor fora do total (3) R$ {sum(OUTSIDE_NOW) / 100:.2f}".replace(".", ",") in everything
    )
    assert "Total arrecadado R$ 7,00" in everything
    assert summary["total_in_cents"] == LATE_AMOUNT


def test_without_a_period_the_month_is_the_current_one(contrib_scene: Scene, login: Login) -> None:
    scene = contrib_scene
    now = datetime.now(ZoneInfo("America/Sao_Paulo"))

    response = login(scene.users["treasurer"]).get(contributions_path(scene.fresh.school, None))

    assert response.status_code == 200
    assert (
        f"contribuicoes-{now.year:04d}-{now.month:02d}.pdf"
        in response.headers["content-disposition"]
    )


def test_an_empty_month_still_gives_a_pdf_with_zeros(contrib_scene: Scene, login: Login) -> None:
    scene = contrib_scene

    response = login(scene.users["treasurer"]).get(
        contributions_path(scene.fresh.school, "2024-01")
    )

    assert response.status_code == 200
    everything = text_of(response.content)
    assert (
        "Quantidade de contribuições 0" in everything and "Total arrecadado R$ 0,00" in everything
    )
    assert "Subtotal (0) R$ 0,00" in everything


def test_the_viewer_gets_initials_instead_of_names(contrib_scene: Scene, login: Login) -> None:
    scene = contrib_scene

    everything = march_text(scene, login, role="viewer")

    for name in (
        "Maria",
        "Exemplo",
        "João",
        "Carla",
        "Pedro",
        "Quatro",
        "Ana Quinze",
        "Doador",
        "Generoso",
        "Aluno Um",
    ):
        assert name not in everything, name
    for initials in ("M. E.", "J. T.", "C. T.", "P. Q.", "A. Q.", "D. G.", "A. U."):
        assert initials in everything, initials
    assert "Contribuinte anônimo" in everything
    assert "5º A" not in everything and "Marta" not in everything  # the class is free text
    assert "Versão sem dados pessoais" in everything
    assert "Subtotal (6) R$ 155,00" in everything  # the same figures


@pytest.mark.parametrize(
    ("role", "status"),
    [("admin", 200), ("school_admin", 200), ("treasurer", 200), ("viewer", 200), ("staff", 403)],
)
def test_who_may_download_it(contrib_scene: Scene, login: Login, role: str, status: int) -> None:
    scene = contrib_scene

    response = login(scene.users[role]).get(contributions_path(scene.fresh.school))

    assert response.status_code == status, response.text


def test_another_school_is_a_404_and_its_people_never_show(
    contrib_scene: Scene, login: Login
) -> None:
    scene = contrib_scene
    api = login(scene.users["treasurer"])

    foreign = api.get(contributions_path(scene.other.school))
    sibling = api.get(contributions_path(scene.sibling_school))
    unknown = api.get(contributions_path("00000000-0000-0000-0000-000000000000"))

    for response in (foreign, sibling, unknown):
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"
    admin = login(scene.users["admin"])  # the whole organization: the sibling is reachable, empty
    assert admin.get(contributions_path(scene.sibling_school)).status_code == 200
    assert admin.get(contributions_path(scene.other.school)).status_code == 404
    assert "Pessoa Alheia" not in march_text(scene, login)
    assert "Pessoa Alheia" not in march_text(scene, login, role="admin")


@pytest.mark.parametrize("period", ["2025-13", "2025-3", "March", "2025-03-01", ""])
def test_a_bad_period_is_a_422(contrib_scene: Scene, login: Login, period: str) -> None:
    scene = contrib_scene

    response = login(scene.users["treasurer"]).get(contributions_path(scene.fresh.school, period))

    assert response.status_code == 422, response.text


def test_sections_that_disagree_with_the_summary_give_no_pdf(
    contrib_scene: Scene, login: Login, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = contrib_scene
    api = login(scene.users["treasurer"])
    # The ledger "moves" between the reads: the list comes back short, twice in a row.
    monkeypatch.setattr("app.reports.routes.queries.settled_contributions", lambda *a, **k: [])

    response = api.get(contributions_path(scene.fresh.school))

    assert (response.status_code, response.json()["code"]) == (409, "report_mismatch")
