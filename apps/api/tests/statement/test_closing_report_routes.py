# ruff: noqa: E501  (SQL text)
"""The PDF of a closing over HTTP: drawn from the snapshot, the sections close with the summary, the
`viewer` gets no names, `report_ref` is written once."""

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from tests.authsupport import Api
from tests.statement.conftest import MARCH, Login, Scene
from tests.statement.pdftext import pages_of, text_of
from tests.statement.test_closing_routes import closings, reopen


def report_path(scene: Scene, closing_id: str) -> str:
    return closings(scene.fresh.school, f"/{closing_id}/report.pdf")


def close_march(login: Login, scene: Scene, **body: Any) -> dict[str, Any]:
    response = login(scene.users["treasurer"]).post(
        closings(scene.fresh.school), {"period": MARCH, **body}
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def test_the_pdf_of_march_has_the_summary_the_sections_and_the_signatures(
    scene: Scene, login: Login
) -> None:
    created = close_march(login, scene, bank_balance_reported_cents=12000)
    api = login(scene.users["treasurer"])

    response = api.get(report_path(scene, created["id"]))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert (
        response.headers["content-disposition"] == 'attachment; filename="fechamento-2025-03.pdf"'
    )
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content.startswith(b"%PDF-")
    pages = pages_of(response.content)
    everything = " ".join(pages)
    first = pages[0]
    # identification and the verification code
    assert "Org T5" in first and "School T5" in first and "março de 2025" in first
    assert created["entries_hash"][:32] in first and created["entries_hash"][32:] in first
    # the box of the period: every value is a column of the snapshot
    for value in (
        "R$ 100,00",  # opening balance
        "R$ 85,00",  # total in
        "R$ 63,50",  # total out
        "SALDO FINAL EM CAIXA R$ 121,50",
        "R$ 15,00",  # pending reimbursements
        "R$ 106,50",  # balance after them
        "R$ 120,00",  # the bank balance reported
        "-R$ 1,50",  # the difference
    ):
        assert value in first, value
    # the sections, each with its subtotal and count
    for heading in (
        "A. Entradas",
        "B. Saídas",
        "C. Devoluções",
        "D. Tarifas bancárias",
        "E. Pendências (fora do saldo)",
    ):
        assert heading in everything, heading
    for subtotal in (
        "Subtotal de entradas (2) R$ 80,00",
        "Subtotal de saídas (2) R$ 60,00",
        "Subtotal de devoluções (1) R$ 5,00",
        "Subtotal de tarifas (1) R$ 3,50",
        "Total pendente (1) R$ 15,00",
    ):
        assert subtotal in everything, subtotal
    # the lines
    for line in ("Maria Exemplo", "João Teste", "APM-000002", "Material de pintura", "Reembolsado"):
        assert line in everything, line
    # every page: "Página x de y" and the code; the last one: the signatures
    for number, page in enumerate(pages, start=1):
        assert f"Página {number} de {len(pages)}" in page
        assert created["entries_hash"] in page
    assert "Tesoureiro(a)" in pages[-1] and "Presidente ou diretor(a)" in pages[-1]


def test_the_sections_close_with_the_summary_of_the_closing(scene: Scene, login: Login) -> None:
    created = close_march(login, scene)
    api = login(scene.users["treasurer"])

    everything = text_of(api.get(report_path(scene, created["id"])).content)

    assert (
        "A + C (entradas e devoluções) R$ 85,00 Entradas do quadro do período R$ 85,00"
        in everything
    )
    assert "B + D (saídas e tarifas) R$ 63,50 Saídas do quadro do período R$ 63,50" in everything
    assert (
        "Saldo inicial + A + C - B - D R$ 121,50 Saldo final em caixa do quadro do período R$ 121,50"
        in everything
    )
    assert "não informado" in everything  # no bank balance was given


def test_where_the_money_went_comes_from_the_breakdown(scene: Scene, login: Login) -> None:
    created = close_march(login, scene)

    everything = text_of(
        login(scene.users["treasurer"]).get(report_path(scene, created["id"])).content
    )

    assert "Para onde foi o dinheiro" in everything
    assert "Reembolso de professor (63,0%) R$ 40,00" in everything  # 4000 of 6350
    assert "Compra de material (31,5%) R$ 20,00" in everything  # 2000 of 6350
    assert "Tarifas bancárias (5,5%) R$ 3,50" in everything  # 350 of 6350


def test_the_viewer_gets_the_version_without_names(scene: Scene, login: Login) -> None:
    created = close_march(login, scene)

    response = login(scene.users["viewer"]).get(report_path(scene, created["id"]))

    assert response.status_code == 200, response.text
    everything = text_of(response.content)
    for name in ("Maria", "Exemplo", "João", "Teste", "T5 staff", "Ana Antiga"):
        assert name not in everything, name
    assert "M. E." in everything and "J. T." in everything and "T. S." in everything
    assert "Versão sem dados pessoais" in everything
    # the figures are the same ones
    assert "SALDO FINAL EM CAIXA R$ 121,50" in everything
    assert "Subtotal de entradas (2) R$ 80,00" in everything


@pytest.mark.parametrize(
    ("role", "status"),
    [("admin", 200), ("school_admin", 200), ("treasurer", 200), ("viewer", 200), ("staff", 403)],
)
def test_who_may_download_the_pdf(scene: Scene, login: Login, role: str, status: int) -> None:
    created = close_march(login, scene)

    response = login(scene.users[role]).get(report_path(scene, created["id"]))

    assert response.status_code == status, response.text


def test_the_reference_is_written_once_on_the_first_pdf_and_never_again(
    scene: Scene, login: Login, admin_engine: Engine
) -> None:
    created = close_march(login, scene)
    assert created["report_ref"] is None
    treasurer = login(scene.users["treasurer"])

    first = treasurer.get(report_path(scene, created["id"]))
    detail = treasurer.get(closings(scene.fresh.school, f"/{created['id']}")).json()
    second = login(scene.users["viewer"]).get(report_path(scene, created["id"]))
    again = treasurer.get(closings(scene.fresh.school, f"/{created['id']}")).json()

    assert first.status_code == second.status_code == 200
    assert detail["report_ref"].startswith("pdf-v1:")
    assert detail["report_ref"].endswith(f":{created['entries_hash'][:16]}")
    assert again["report_ref"] == detail["report_ref"]  # the second PDF did not rewrite it
    with admin_engine.connect() as conn:
        updates: int = conn.execute(
            text(
                "SELECT count(*) FROM audit_logs WHERE entity_id = :c AND action = 'monthly_closings.update'"
            ),
            {"c": created["id"]},
        ).scalar_one()
    assert updates == 1


def test_a_reopened_closing_has_no_pdf(scene: Scene, login: Login) -> None:
    created = close_march(login, scene)
    assert reopen(login(scene.users["admin"]), scene, created["id"]).status_code == 200

    response = login(scene.users["treasurer"]).get(report_path(scene, created["id"]))

    assert (response.status_code, response.json()["code"]) == (409, "closing_reopened")


def test_a_ledger_that_no_longer_matches_the_snapshot_gets_no_pdf(
    scene: Scene, login: Login, admin_engine: Engine
) -> None:
    created = close_march(login, scene)
    with admin_engine.begin() as conn:  # what only someone who can switch the triggers off could do
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        conn.execute(
            text("UPDATE financial_transactions SET amount_cents = amount_cents + 1 WHERE id = :t"),
            {"t": scene.entries["maria"]},
        )

    response = login(scene.users["treasurer"]).get(report_path(scene, created["id"]))

    assert (response.status_code, response.json()["code"]) == (409, "closing_mismatch")
    detail = login(scene.users["treasurer"]).get(closings(scene.fresh.school, f"/{created['id']}"))
    assert detail.json()["report_ref"] is None


def test_the_pdf_of_another_organization_is_a_404(scene: Scene, login: Login) -> None:
    theirs = login(scene.other_users["treasurer"]).post(
        closings(scene.other.school), {"period": MARCH}
    )
    assert theirs.status_code == 201
    mine = login(scene.users["treasurer"])

    answers = [
        mine.get(report_path(scene, theirs.json()["id"])),  # their closing under my school
        mine.get(closings(scene.other.school, f"/{theirs.json()['id']}/report.pdf")),
        mine.get(report_path(scene, str(uuid.uuid4()))),
    ]

    for response in answers:
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"
        assert response.headers["content-type"].startswith("application/problem+json")


def test_the_same_closing_gives_the_same_pdf_but_for_the_time_it_was_made(
    scene: Scene, login: Login
) -> None:
    created = close_march(login, scene)
    api: Api = login(scene.users["treasurer"])

    first = api.get(report_path(scene, created["id"])).content
    second = api.get(report_path(scene, created["id"])).content

    # Only the "Gerado em" line differs (to the minute, usually not even that).
    assert len(pages_of(first)) == len(pages_of(second))

    def before_generated(content: bytes) -> list[str]:
        return [page.split("Gerado em")[0] for page in pages_of(content)]

    assert before_generated(first) == before_generated(second)
