"""The PDF of the contributions of a month: who gave what, through which channel.

Same rule as the closing report: `build_contributions_report` sorts what the database returned and
CHECKS it against `statement_summary` of the same period before anything is drawn:

* section A (contributions of the guardians) adds up to `contributions_in_cents`;
* section B (other income: donations and the like) adds up to `other_in_cents`.

What is not in the cash ledger (waiting for payment, in review, cancelled, expired) is listed apart,
in "Fora do total", and never enters a sum.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from reportlab.platypus import Flowable, PageBreak, Spacer

from app.reports.closing_report import ANONYMOUS, STATUS_LABELS, ReportMismatch
from app.reports.format import (
    brl,
    initials,
    local_date,
    local_datetime,
    month_name,
    percent,
    reference,
    short_id,
)
from app.reports.layout import (
    Column,
    build_pdf,
    data_table,
    identification_table,
    key_value_table,
    para,
    section,
)

CHANNEL_LABELS = {
    "PIX": "Pix pela plataforma",
    "PIX_DIRECT": "Pix direto na conta",
    "CASH": "Dinheiro",
    "TRANSFER": "Transferência",
    "OTHER": "Outro",
}
UNSETTLED_LABELS = {
    "PENDING_PAYMENT": "Aguardando pagamento",
    "REVIEW_REQUIRED": "Em análise",
    "CANCELLED": "Cancelada",
    "EXPIRED": "Expirada",
}
FIXED_AMOUNTS_SHOWN = 5  # the most frequent amounts get a row of their own; the rest are "outros"


@dataclass(frozen=True)
class Contribution:
    reference_code: int
    when: datetime
    guardian: str
    student: str
    class_name: str
    channel: str
    pix_id: str
    amount_cents: int
    status: str
    late_adjustment: bool


@dataclass(frozen=True)
class ContributionsReport:
    organization_name: str
    school_name: str
    timezone: str
    period_start: date
    period_end: date
    generated_at: datetime
    personal_data: bool
    guardians: list[Contribution]  # A: group CONTRIBUTIONS, in the cash ledger
    others: list[Contribution]  # B: the other groups, in the cash ledger
    outside: list[Contribution]  # C: not in the cash ledger, not in any total


def _person(name: str | None, personal_data: bool) -> str:
    if not name:
        return ""
    return name if personal_data else initials(name)


def _contribution(row: dict[str, Any], personal_data: bool, *, settled: bool) -> Contribution:
    status = row["status"]
    return Contribution(
        reference_code=row["reference_code"],
        when=row["settled_at"] if settled else row["occurred_at"],
        guardian=_person(row["guardian_name"], personal_data) or ANONYMOUS,
        student=_person(row["student_name"], personal_data),
        # The class is typed by the guardian: free text, so only management gets it.
        class_name=(row["class_name"] or "") if personal_data else "",
        channel=CHANNEL_LABELS.get(row["method"], "Outro"),
        pix_id=short_id(row["pix_id"]),
        amount_cents=row["amount_cents"],
        status=(STATUS_LABELS if settled else UNSETTLED_LABELS).get(status, status),
        late_adjustment=bool(row["late_adjustment"]),
    )


def _sum(items: list[Contribution]) -> int:
    return sum(item.amount_cents for item in items)


def build_contributions_report(
    *,
    settled: list[dict[str, Any]],
    unsettled: list[dict[str, Any]],
    summary: dict[str, Any],
    organization_name: str,
    school_name: str,
    period_start: date,
    period_end: date,
    generated_at: datetime,
    personal_data: bool,
) -> ContributionsReport:
    """Sort the rows into the sections and prove they add up to `statement_summary`."""
    guardians: list[Contribution] = []
    others: list[Contribution] = []
    for row in settled:
        target = guardians if row["report_group"] == "CONTRIBUTIONS" else others
        target.append(_contribution(row, personal_data, settled=True))
    if (
        _sum(guardians) != summary["contributions_in_cents"]
        or _sum(others) != summary["other_in_cents"]
    ):
        raise ReportMismatch("contributions")
    return ContributionsReport(
        organization_name=organization_name,
        school_name=school_name,
        timezone=str(summary["timezone"]),
        period_start=period_start,
        period_end=period_end,
        generated_at=generated_at,
        personal_data=personal_data,
        guardians=guardians,
        others=others,
        outside=[_contribution(row, personal_data, settled=False) for row in unsettled],
    )


# --- drawing -------------------------------------------------------------------------------------


def _summary(report: ContributionsReport) -> list[Flowable]:
    items = report.guardians
    count, total = len(items), _sum(items)
    rows: list[tuple[str, str, str]] = [
        ("Quantidade de contribuições", str(count), "normal"),
        ("Total arrecadado", brl(total), "big"),
    ]
    if count:
        amounts = [item.amount_cents for item in items]
        average = (total + count // 2) // count
        rows.extend(
            [
                ("Menor contribuição", brl(min(amounts)), "normal"),
                ("Maior contribuição", brl(max(amounts)), "normal"),
                ("Valor médio", brl(average), "normal"),
            ]
        )
    story: list[Flowable] = [para("Resumo", "h2"), key_value_table(rows)]

    story.append(para("Quantidade por valor", "h2"))
    by_amount = Counter(item.amount_cents for item in items)
    ranked = sorted(by_amount.items(), key=lambda pair: (-pair[1], pair[0]))
    shown = sorted(ranked[:FIXED_AMOUNTS_SHOWN])
    shown_amounts = {amount for amount, _ in shown}
    value_rows: list[list[object]] = [
        [brl(amount), str(quantity), brl(amount * quantity), percent(amount * quantity, total)]
        for amount, quantity in shown
    ]
    rest = [item for item in items if item.amount_cents not in shown_amounts]
    if rest:
        value_rows.append(
            ["Outros valores", str(len(rest)), brl(_sum(rest)), percent(_sum(rest), total)]
        )
    story.append(_distribution(["Valor", "Quantidade", "Total", "% do total"], value_rows))

    story.append(para("Quantidade por canal", "h2"))
    channel_rows: list[list[object]] = []
    for label in CHANNEL_LABELS.values():
        group = [item for item in items if item.channel == label]
        if group:
            channel_rows.append(
                [label, str(len(group)), brl(_sum(group)), percent(_sum(group), total)]
            )
    story.append(_distribution(["Canal", "Quantidade", "Total", "% do total"], channel_rows))
    return story


def _distribution(titles: list[str], rows: list[list[object]]) -> Flowable:
    columns = [Column(titles[0], 34), Column(titles[1], 16, right=True)]
    columns += [Column(titles[2], 25, right=True), Column(titles[3], 25, right=True)]
    if not rows:
        return para("Nenhuma contribuição neste período.", "body")
    return data_table(columns, rows)


def _list_columns(*, with_student: bool) -> list[Column]:
    columns = [Column("Data e hora", 15), Column("Referência", 11), Column("Responsável", 18)]
    if with_student:
        columns.append(Column("Aluno e turma", 14))
    return [
        *columns,
        Column("Canal", 12),
        Column("Pix", 15),
        Column("Situação", 12),
        Column("Valor", 11, right=True),
    ]


def _list_rows(
    report: ContributionsReport, items: list[Contribution], *, with_student: bool
) -> list[list[str]]:
    rows = []
    for item in items:
        row = [
            local_datetime(item.when, report.timezone),
            reference(item.reference_code),
            item.guardian,
        ]
        if with_student:
            row.append(" | ".join(part for part in (item.student, item.class_name) if part) or "—")
        row.extend(
            [
                item.channel,
                item.pix_id,
                item.status + (" (ajuste tardio)" if item.late_adjustment else ""),
                brl(item.amount_cents),
            ]
        )
        rows.append(row)
    return rows


def render_contributions_report(report: ContributionsReport) -> bytes:
    start = report.period_start
    everything = [*report.guardians, *report.others, *report.outside]
    with_student = any(item.student or item.class_name for item in everything)
    columns = _list_columns(with_student=with_student)

    story: list[Flowable] = [
        para("Relatório de contribuições dos responsáveis", "title"),
        para(
            f"{month_name(start).capitalize()}  |  {local_date(start)} a "
            f"{local_date(report.period_end)}",
            "subtitle",
        ),
        Spacer(1, 6),
        identification_table(
            [
                ("APM", report.organization_name),
                ("Escola", report.school_name),
                ("Período", month_name(start)),
                ("Gerado em", local_datetime(report.generated_at, report.timezone)),
                ("Fuso horário", report.timezone),
            ]
        ),
        Spacer(1, 6),
    ]
    if not report.personal_data:
        story.extend(
            [
                para("Versão sem dados pessoais: os nomes aparecem pelas iniciais.", "notice"),
                Spacer(1, 4),
            ]
        )
    story.extend(_summary(report))
    story.extend(
        [
            Spacer(1, 6),
            para(
                "O total conta somente o que já entrou no caixa no período. Contribuições "
                "aguardando pagamento, em análise, canceladas ou expiradas aparecem apenas na "
                'seção "Fora do total".',
                "small",
            ),
            PageBreak(),
            para("Lista de contribuições", "title"),
        ]
    )
    story.extend(
        section(
            "A. Contribuições dos responsáveis",
            columns,
            _list_rows(report, report.guardians, with_student=with_student),
            subtotal_label="Subtotal",
            count=len(report.guardians),
            total_cents=_sum(report.guardians),
            empty_text="Nenhuma contribuição neste período.",
        )
    )
    story.extend(
        section(
            "B. Outras entradas (doações e demais categorias)",
            columns,
            _list_rows(report, report.others, with_student=with_student),
            subtotal_label="Subtotal",
            count=len(report.others),
            total_cents=_sum(report.others),
            empty_text="Nenhuma entrada neste período.",
        )
    )
    story.extend(
        section(
            "C. Fora do total (não entram no caixa)",
            columns,
            _list_rows(report, report.outside, with_student=with_student),
            subtotal_label="Valor fora do total",
            count=len(report.outside),
            total_cents=_sum(report.outside),
            empty_text="Nenhuma contribuição pendente, em análise ou cancelada.",
            note="A data desta seção é a do fato (quando a contribuição foi pedida ou registrada).",
        )
    )
    story.append(para("Conferência com o resumo do banco de dados", "h2"))
    story.append(
        key_value_table(
            [
                ("A. Contribuições dos responsáveis", brl(_sum(report.guardians)), "normal"),
                ("B. Outras entradas", brl(_sum(report.others)), "normal"),
                (
                    "Total de entradas por contribuição (A + B)",
                    brl(_sum(report.guardians) + _sum(report.others)),
                    "medium",
                ),
            ]
        )
    )
    period = f"{start.year:04d}-{start.month:02d}"
    return build_pdf(
        story,
        title=f"Contribuições {period} - {report.school_name}",
        header=(
            f"{report.organization_name} | {report.school_name} | "
            f"Contribuições de {month_name(start)}"
        ),
        footer_code=(
            f"Contribuições de {period}: {len(report.guardians)} no total de "
            f"{brl(_sum(report.guardians))}"
        ),
        footer_note="Valores em reais; datas e horas no fuso horário da escola.",
    )
