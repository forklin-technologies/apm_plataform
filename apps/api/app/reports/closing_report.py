"""The PDF of a monthly closing, drawn from its SNAPSHOT.

Two steps, so that nothing is recomputed while drawing:

1. `build_closing_report` takes the stored closing and the entries of its (closed, therefore
   immutable) period, sorts the entries into the sections A to D, and CHECKS that what the sections
   add up to is exactly what the closing says. A closing that does not reconcile raises
   `ReportMismatch`: no page is drawn from numbers that disagree.
2. `render_closing_report` writes the pages. Every money value on page 1 is a column of the closing;
   every subtotal is the sum of the lines above it, which step 1 proved equals the column.

To add a section: build its lines in `build_closing_report`, draw it in `render_closing_report`
with `section(...)`, and add the cross-check that ties it to a column of the closing.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.reports.format import (
    brl,
    initials,
    local_date,
    local_datetime,
    month_name,
    percent,
    reference,
)
from app.reports.layout import (
    CONTENT_WIDTH,
    STYLES,
    Column,
    build_pdf,
    key_value_table,
    para,
    section,
)

ANONYMOUS = "Contribuinte anônimo"
KIND_LABELS = {
    "CONTRIBUTION": "Contribuição",
    "EXPENSE": "Despesa",
    "REIMBURSEMENT": "Reembolso",
    "REFUND": "Devolução",
}
STATUS_LABELS = {
    "PAID": "Pago",
    "CONFIRMED": "Confirmada",
    "REIMBURSED": "Reembolsado",
}
ORIGIN_LABELS = {
    "GUARDIAN": "responsável",
    "TEACHER": "professor(a)",
    "DIRECTOR": "diretor(a)",
    "EMPLOYEE": "funcionário(a)",
    "MANAGEMENT": "gestão",
    "APM": "APM",
    "BANK": "banco",
    "OTHER": "outro",
}
SECTION_LABELS = {
    "AWAITING_APPROVAL": "Aguardando aprovação",
    "AWAITING_CORRECTION": "Aguardando correção",
    "REVIEW": "Em análise (valor divergente ou Pix direto)",
    "RECEIVABLE": "A receber",
    "PAYABLE": "A pagar",
}


class ReportMismatch(Exception):  # noqa: N818  (a domain condition, not an error of the program)
    """The entries of the period do not add up to the figures of the closing."""


@dataclass(frozen=True)
class Line:
    reference_code: int
    kind: str
    group: str
    when: datetime
    who: str
    type_label: str
    description: str
    category: str
    status: str
    amount_cents: int
    late_adjustment: bool


@dataclass(frozen=True)
class PendingLine:
    reference_code: int
    occurred_at: datetime
    kind_label: str
    section_label: str
    amount_cents: int


@dataclass(frozen=True)
class ClosingReport:
    organization_name: str
    school_name: str
    generated_at: datetime
    personal_data: bool
    closing: dict[str, Any]  # the stored snapshot: the row of monthly_closings
    category_names: dict[str, str]
    incoming: list[Line]  # A: contributions
    outgoing: list[Line]  # B: expenses and reimbursements, without the bank fees
    refunds: list[Line]  # C
    fees: list[Line]  # D
    pending: list[PendingLine]  # E, as of now

    @property
    def timezone(self) -> str:
        return str(self.closing["timezone"])


def _who(entry: dict[str, Any], personal_data: bool) -> str:
    """Who the money came from or went to. A contribution without a name is anonymous."""
    if entry["kind"] == "REIMBURSEMENT":
        name = entry["beneficiary_label"] or entry["origin_label"]
    else:
        name = entry["origin_label"]
    if entry["kind"] == "CONTRIBUTION" and not name:
        return ANONYMOUS
    if not name:
        return "—"
    return str(name) if personal_data else initials(str(name))


def _line(entry: dict[str, Any], names: dict[str, str], personal_data: bool) -> Line:
    return Line(
        reference_code=entry["reference_code"],
        kind=entry["kind"],
        group=entry["report_group"],
        when=entry["settled_at"],
        who=_who(entry, personal_data),
        type_label=ORIGIN_LABELS.get(entry["origin_type"], "outro"),
        description=str(entry["description"] or ""),
        category=names.get(entry["category_key"], entry["category_key"]),
        status=STATUS_LABELS.get(entry["status_label"], entry["status_label"]),
        amount_cents=entry["amount_cents"],
        late_adjustment=bool(entry["late_adjustment"]),
    )


def _sum(lines: list[Line]) -> int:
    return sum(line.amount_cents for line in lines)


def build_closing_report(
    *,
    closing: dict[str, Any],
    entries: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    category_names: dict[str, str],
    organization_name: str,
    school_name: str,
    generated_at: datetime,
    personal_data: bool,
) -> ClosingReport:
    """Sort the entries into the sections and prove they reconcile with the closing."""
    names = category_names
    incoming, outgoing, refunds, fees = [], [], [], []
    by_kind: dict[str, int] = {}
    contributions_in = 0
    for entry in entries:
        line = _line(entry, names, personal_data)
        by_kind[entry["kind"]] = by_kind.get(entry["kind"], 0) + entry["amount_cents"]
        if entry["kind"] == "CONTRIBUTION":
            incoming.append(line)
            if entry["report_group"] == "CONTRIBUTIONS":
                contributions_in += entry["amount_cents"]
        elif entry["kind"] == "REFUND":
            refunds.append(line)
        elif entry["report_group"] == "BANK_FEES":
            fees.append(line)
        else:
            outgoing.append(line)

    expected = {
        "contributions_in_cents": contributions_in,
        "other_in_cents": by_kind.get("CONTRIBUTION", 0) - contributions_in,
        "refunds_in_cents": by_kind.get("REFUND", 0),
        "expenses_out_cents": by_kind.get("EXPENSE", 0),
        "reimbursements_out_cents": by_kind.get("REIMBURSEMENT", 0),
        "entries_count": len(entries),
    }
    for column, value in expected.items():
        if closing[column] != value:
            raise ReportMismatch(column)
    if (
        _sum(incoming) + _sum(refunds) != closing["total_in_cents"]
        or _sum(outgoing) + _sum(fees) != closing["total_out_cents"]
        or closing["opening_balance_cents"] + closing["total_in_cents"] - closing["total_out_cents"]
        != closing["closing_balance_cents"]
    ):
        raise ReportMismatch("totals")

    return ClosingReport(
        organization_name=organization_name,
        school_name=school_name,
        generated_at=generated_at,
        personal_data=personal_data,
        closing=closing,
        category_names=names,
        incoming=incoming,
        outgoing=outgoing,
        refunds=refunds,
        fees=fees,
        pending=[
            PendingLine(
                reference_code=row["reference_code"],
                occurred_at=row["occurred_at"],
                kind_label=KIND_LABELS.get(row["kind"], row["kind"]),
                section_label=SECTION_LABELS.get(row["section"], row["section"]),
                amount_cents=row["amount_cents"],
            )
            for row in pending
        ],
    )


# --- drawing -------------------------------------------------------------------------------


def _summary_page(report: ClosingReport) -> list[Flowable]:
    c = report.closing
    tz = report.timezone
    fees_out = _sum(report.fees)
    others = len(report.incoming) - _count(report.incoming, group="CONTRIBUTIONS")
    start, end = c["period_start"], c["period_end"]
    story: list[Flowable] = [
        para("Prestação de contas: fechamento mensal", "title"),
        para(
            f"{month_name(start).capitalize()}  |  {local_date(start)} a {local_date(end)}",
            "subtitle",
        ),
        Spacer(1, 6),
        _identification(report),
        Spacer(1, 8),
    ]
    if not report.personal_data:
        story.extend(
            [
                para("Versão sem dados pessoais: as origens aparecem pelas iniciais.", "notice"),
                Spacer(1, 4),
            ]
        )
    story.append(para("Quadro do período", "h2"))
    rows: list[tuple[str, str, str]] = [
        ("Saldo inicial", brl(c["opening_balance_cents"]), "normal"),
        ("(+) Entradas", brl(c["total_in_cents"]), "medium"),
        (
            f"Contribuições ({_count(report.incoming, group='CONTRIBUTIONS')})",
            brl(c["contributions_in_cents"]),
            "sub",
        ),
        (
            f"Outras entradas ({others})",
            brl(c["other_in_cents"]),
            "sub",
        ),
        (f"Devoluções confirmadas ({len(report.refunds)})", brl(c["refunds_in_cents"]), "sub"),
        ("(-) Saídas", brl(c["total_out_cents"]), "medium"),
        (
            f"Reembolsos pagos ({_count(report.outgoing, kind='REIMBURSEMENT')})",
            brl(c["reimbursements_out_cents"]),
            "sub",
        ),
        (
            f"Despesas pagas pela APM ({_count(report.outgoing, kind='EXPENSE')})",
            brl(c["expenses_out_cents"] - fees_out),
            "sub",
        ),
        (f"Tarifas bancárias ({len(report.fees)})", brl(fees_out), "sub"),
        ("SALDO FINAL EM CAIXA", brl(c["closing_balance_cents"]), "big"),
        (
            "Reembolsos pendentes (na data do fechamento)",
            brl(c["pending_reimbursements_cents"]),
            "normal",
        ),
        ("Saldo após os reembolsos pendentes", brl(c["closing_after_pending_cents"]), "medium"),
    ]
    story.append(key_value_table(rows))
    story.append(Spacer(1, 8))

    story.append(para("Conciliação com o banco", "h2"))
    reported = c["bank_balance_reported_cents"]
    story.append(
        key_value_table(
            [
                (
                    "Saldo segundo o banco (informado no fechamento)",
                    "não informado" if reported is None else brl(reported),
                    "normal",
                ),
                (
                    "Saldo segundo o APM Digital (em caixa)",
                    brl(c["closing_balance_cents"]),
                    "normal",
                ),
                (
                    "Diferença (banco menos APM Digital)",
                    "—" if c["bank_difference_cents"] is None else brl(c["bank_difference_cents"]),
                    "medium",
                ),
            ]
        )
    )
    story.append(Spacer(1, 8))

    story.append(para("Código de verificação do fechamento", "h2"))
    story.append(
        Paragraph(
            escape(c["entries_hash"][:32]) + "<br/>" + escape(c["entries_hash"][32:]),
            STYLES["code"],
        )
    )
    story.append(Spacer(1, 3))
    story.append(
        para(
            f"Fechado em {local_datetime(c['closed_at'], tz)}. O código é o SHA-256 dos "
            "lançamentos do "
            "período: se qualquer lançamento mudasse, o código deixaria de conferir.",
            "small",
        )
    )
    story.extend(_where_it_went(report))
    return story


def _identification(report: ClosingReport) -> Table:
    c = report.closing
    rows = [
        ("APM", report.organization_name),
        ("Escola", report.school_name),
        ("Período", month_name(c["period_start"])),
        ("Gerado em", local_datetime(report.generated_at, report.timezone)),
        ("Fuso horário", report.timezone),
    ]
    table = Table(
        [[para(label, "cell_bold"), para(value, "cell")] for label, value in rows],
        colWidths=[CONTENT_WIDTH * 0.2, CONTENT_WIDTH * 0.8],
    )
    table.setStyle(
        TableStyle([("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)])
    )
    return table


def _count(lines: list[Line], *, kind: str | None = None, group: str | None = None) -> int:
    return sum(
        1
        for line in lines
        if (kind is None or line.kind == kind) and (group is None or line.group == group)
    )


def _where_it_went(report: ClosingReport) -> list[Flowable]:
    """Outflows and inflows by category, from the BREAKDOWN of the snapshot (never recomputed)."""
    c = report.closing
    out_rows: list[tuple[str, int]] = []
    in_rows: list[tuple[str, int]] = []
    for body in c["breakdown"].values():
        for key, figures in body["categories"].items():
            name = report.category_names.get(key, key)
            if figures["out"]:
                out_rows.append((name, figures["out"]))
            if figures["in"]:
                in_rows.append((name, figures["in"]))
    story: list[Flowable] = []
    for title, rows, whole in (
        ("Para onde foi o dinheiro (saídas por categoria)", out_rows, c["total_out_cents"]),
        ("De onde veio o dinheiro (entradas por categoria)", in_rows, c["total_in_cents"]),
    ):
        story.append(para(title, "h2"))
        if not rows:
            story.append(para("Nenhum lançamento neste período.", "body"))
            continue
        ordered = sorted(rows, key=lambda row: (-row[1], row[0]))
        story.append(
            key_value_table(
                [
                    (f"{name}  ({percent(value, whole)})", brl(value), "sub")
                    for name, value in ordered
                ]
            )
        )
    return story


_WHEN = Column("Data e hora", 13)
_REF = Column("Referência", 10)
_AMOUNT = Column("Valor", 11, right=True)


def _entry_rows(
    report: ClosingReport, lines: list[Line], *, who_title: str
) -> tuple[list[Column], list[list[str]]]:
    tz = report.timezone
    columns = [
        _WHEN,
        _REF,
        Column(who_title, 20),
        Column("Finalidade e categoria", 27),
        Column("Situação", 11),
        _AMOUNT,
    ]
    rows = [
        [
            local_datetime(line.when, tz) + (" (ajuste tardio)" if line.late_adjustment else ""),
            reference(line.reference_code),
            line.who if line.who == ANONYMOUS else f"{line.who} ({line.type_label})",
            (f"{line.description}: " if line.description else "") + line.category
            if line.description != line.category
            else line.category,
            line.status,
            brl(line.amount_cents),
        ]
        for line in lines
    ]
    return columns, rows


def _details(report: ClosingReport) -> list[Flowable]:
    c = report.closing
    story: list[Flowable] = [PageBreak(), para("Detalhamento", "title"), Spacer(1, 2)]
    for title, lines, who_title, label in (
        ("A. Entradas (contribuições)", report.incoming, "Origem", "Subtotal de entradas"),
        (
            "B. Saídas (despesas e reembolsos)",
            report.outgoing,
            "Beneficiário ou origem",
            "Subtotal de saídas",
        ),
        ("C. Devoluções confirmadas", report.refunds, "Quem devolveu", "Subtotal de devoluções"),
        ("D. Tarifas bancárias", report.fees, "Origem", "Subtotal de tarifas"),
    ):
        columns, rows = _entry_rows(report, lines, who_title=who_title)
        story.extend(
            section(
                title,
                columns,
                rows,
                subtotal_label=label,
                count=len(lines),
                total_cents=_sum(lines),
            )
        )

    pending_columns = [
        Column("Data", 14),
        _REF,
        Column("Tipo", 18),
        Column("Situação", 38),
        _AMOUNT,
    ]
    pending_rows = [
        [
            local_datetime(line.occurred_at, report.timezone),
            reference(line.reference_code),
            line.kind_label,
            line.section_label,
            brl(line.amount_cents),
        ]
        for line in report.pending
    ]
    story.extend(
        section(
            "E. Pendências (fora do saldo)",
            pending_columns,
            pending_rows,
            subtotal_label="Total pendente",
            count=len(report.pending),
            total_cents=sum(line.amount_cents for line in report.pending),
            note=(
                "Posição na data de geração deste documento. O quadro da primeira página mostra os "
                "reembolsos pendentes na data do fechamento. Nada desta seção entra no saldo."
            ),
        )
    )

    story.append(para("Conferência das seções com o quadro do período", "h2"))
    story.append(
        key_value_table(
            [
                (
                    "A + C (entradas e devoluções)",
                    brl(_sum(report.incoming) + _sum(report.refunds)),
                    "normal",
                ),
                ("Entradas do quadro do período", brl(c["total_in_cents"]), "sub"),
                (
                    "B + D (saídas e tarifas)",
                    brl(_sum(report.outgoing) + _sum(report.fees)),
                    "normal",
                ),
                ("Saídas do quadro do período", brl(c["total_out_cents"]), "sub"),
                (
                    "Saldo inicial + A + C - B - D",
                    brl(
                        c["opening_balance_cents"]
                        + _sum(report.incoming)
                        + _sum(report.refunds)
                        - _sum(report.outgoing)
                        - _sum(report.fees)
                    ),
                    "medium",
                ),
                (
                    "Saldo final em caixa do quadro do período",
                    brl(c["closing_balance_cents"]),
                    "sub",
                ),
            ]
        )
    )
    return story


def _signatures(report: ClosingReport) -> list[Flowable]:
    line = "_" * 44
    table = Table(
        [
            [para(line, "body"), para(line, "body")],
            [para("Tesoureiro(a)", "cell_bold"), para("Presidente ou diretor(a)", "cell_bold")],
            [para("Nome e data:", "small"), para("Nome e data:", "small")],
        ],
        colWidths=[CONTENT_WIDTH / 2] * 2,
    )
    table.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, 0), 24)]))
    return [
        KeepTogether(
            [
                Spacer(1, 6 * mm),
                para("Assinaturas", "h2"),
                para(
                    "Declaramos que as informações deste relatório correspondem aos registros da "
                    "plataforma na data do fechamento.",
                    "body",
                ),
                table,
            ]
        )
    ]


def render_closing_report(report: ClosingReport) -> bytes:
    """The PDF of the closing: summary, details A to E, the reconciliation of the sections and the
    page of signatures."""
    c = report.closing
    story = [*_summary_page(report), *_details(report), *_signatures(report)]
    period = f"{c['period_start'].year:04d}-{c['period_start'].month:02d}"
    return build_pdf(
        story,
        title=f"Fechamento {period} - {report.school_name}",
        header=(
            f"{report.organization_name} | {report.school_name} | "
            f"Fechamento de {month_name(c['period_start'])}"
        ),
        footer_code=f"Código de verificação: {c['entries_hash']}",
        footer_note=(
            "Os comprovantes estão arquivados na plataforma pelos números de referência citados."
        ),
    )
