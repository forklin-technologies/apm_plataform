"""The pieces both reports are made of: the page, the footer "Página x de y", and the tables.

reportlab draws it (pure Python, no system package). The fonts are the standard PDF ones, so the
file embeds none; `printable()` keeps every character inside what they can draw.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    CondPageBreak,
    Flowable,
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.reports.format import brl, printable

INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#616e7c")
RULE = colors.HexColor("#cbd2d9")
BAND = colors.HexColor("#f0f4f8")
ACCENT = colors.HexColor("#0b5394")

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 15 * mm
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN

_base = getSampleStyleSheet()["Normal"]


def _style(name: str, **kwargs: Any) -> ParagraphStyle:
    options: dict[str, Any] = {"fontName": "Helvetica", "textColor": INK, **kwargs}
    return ParagraphStyle(name, parent=_base, **options)


STYLES = {
    "title": _style("title", fontName="Helvetica-Bold", fontSize=17, leading=21, spaceAfter=2),
    "subtitle": _style("subtitle", fontSize=10, leading=13, textColor=MUTED),
    "h2": _style(
        "h2",
        fontName="Helvetica-Bold",
        fontSize=11.5,
        leading=15,
        spaceBefore=10,
        spaceAfter=4,
    ),
    "body": _style("body", fontSize=8.5, leading=11),
    "small": _style("small", fontSize=7.5, leading=9.5, textColor=MUTED),
    "cell": _style("cell", fontSize=7.5, leading=9),
    "cell_right": _style("cell_right", fontSize=7.5, leading=9, alignment=2),
    "cell_bold": _style("cell_bold", fontName="Helvetica-Bold", fontSize=7.5, leading=9),
    "cell_bold_right": _style(
        "cell_bold_right", fontName="Helvetica-Bold", fontSize=7.5, leading=9, alignment=2
    ),
    "head": _style(
        "head", fontName="Helvetica-Bold", fontSize=7.5, leading=9, textColor=colors.white
    ),
    "head_right": _style(
        "head_right",
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9,
        textColor=colors.white,
        alignment=2,
    ),
    "code": _style("code", fontName="Courier", fontSize=8, leading=10),
    "notice": _style("notice", fontSize=8, leading=10.5, textColor=ACCENT),
}


def para(value: object, style: str = "cell") -> Paragraph:
    """A paragraph of plain text: escaped (a name may hold `<` or `&`) and made printable."""
    return Paragraph(escape(printable(value)), STYLES[style])


@dataclass(frozen=True)
class Column:
    title: str
    width: float  # relative; the columns share the width of the page in this proportion
    right: bool = False


def data_table(
    columns: Sequence[Column],
    rows: Sequence[Sequence[object]],
    *,
    footer: Sequence[object] | None = None,
) -> LongTable:
    """A table with a repeated header row, right-aligned money columns and an optional bold last
    row (the subtotal)."""
    total = sum(column.width for column in columns)
    widths = [CONTENT_WIDTH * column.width / total for column in columns]
    head = [para(c.title, "head_right" if c.right else "head") for c in columns]
    body = [
        [
            para(value, "cell_right" if column.right else "cell")
            for column, value in zip(columns, row, strict=True)
        ]
        for row in rows
    ]
    cells = [head, *body]
    if footer is not None:
        cells.append(
            [
                para(value, "cell_bold_right" if column.right else "cell_bold")
                for column, value in zip(columns, footer, strict=True)
            ]
        )
    table = LongTable(cells, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    style.extend(("BACKGROUND", (0, i), (-1, i), BAND) for i in range(2, len(cells), 2))
    if footer is not None:
        style.extend(
            [
                ("LINEABOVE", (0, -1), (-1, -1), 0.9, INK),
                ("BACKGROUND", (0, -1), (-1, -1), BAND),
                ("SPAN", (0, -1), (-2, -1)),  # the label runs over every column but the amount
            ]
        )
    table.setStyle(TableStyle(style))
    return table


def section(
    title: str,
    columns: Sequence[Column],
    rows: Sequence[Sequence[object]],
    *,
    subtotal_label: str,
    count: int,
    total_cents: int,
    empty_text: str = "Nenhum lançamento neste período.",
    note: str | None = None,
) -> list[Flowable]:
    """A titled table that ends with its subtotal and its count. The heading stays with the first
    rows (it never ends a page alone)."""
    # A heading never ends a page: below 35 mm of room the section starts on the next one.
    pieces: list[Flowable] = [
        CondPageBreak(35 * mm),
        Paragraph(escape(printable(title)), STYLES["h2"]),
    ]
    if note:
        pieces.extend([para(note, "small"), Spacer(1, 3)])
    footer = [""] * len(columns)
    footer[0] = f"{subtotal_label} ({count})"
    footer[-1] = brl(total_cents)
    if not rows:
        pieces.extend([para(empty_text, "body"), Spacer(1, 3)])
    pieces.append(data_table(columns, rows, footer=footer))
    return pieces


def identification_table(rows: Sequence[tuple[str, str]]) -> Table:
    """(label, value) lines under the title: who, which school, which period, when."""
    table = Table(
        [[para(label, "cell_bold"), para(value, "cell")] for label, value in rows],
        colWidths=[CONTENT_WIDTH * 0.2, CONTENT_WIDTH * 0.8],
    )
    table.setStyle(
        TableStyle([("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)])
    )
    return table


def key_value_table(
    rows: Sequence[tuple[str, str, str]], *, label_width: float = 0.64
) -> LongTable:
    """(label, value, style) rows; style is 'normal', 'sub' (indented), 'big' or 'medium'."""
    widths = [CONTENT_WIDTH * label_width, CONTENT_WIDTH * (1 - label_width)]
    fonts = {
        "normal": ("Helvetica", 9, 12),
        "sub": ("Helvetica", 8.5, 11),
        "medium": ("Helvetica-Bold", 10, 13),
        "big": ("Helvetica-Bold", 13, 17),
    }
    cells = []
    style: list[Any] = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for index, (label, value, kind) in enumerate(rows):
        font, size, leading = fonts[kind]
        left = ParagraphStyle(
            f"kv-l{index}", parent=_base, fontName=font, fontSize=size, leading=leading,
            textColor=INK if kind != "sub" else MUTED, leftIndent=10 if kind == "sub" else 0,
        )  # fmt: skip
        right = ParagraphStyle(
            f"kv-r{index}", parent=left, alignment=2, leftIndent=0, textColor=INK
        )
        cells.append(
            [Paragraph(escape(printable(label)), left), Paragraph(escape(printable(value)), right)]
        )
        if kind == "big":
            style.append(("BACKGROUND", (0, index), (-1, index), BAND))
            style.append(("LINEABOVE", (0, index), (-1, index), 1, INK))
        if kind == "medium":
            style.append(("BACKGROUND", (0, index), (-1, index), BAND))
    table = LongTable(cells, colWidths=widths)
    table.setStyle(TableStyle(style))
    return table


class NumberedCanvas(canvas.Canvas):  # type: ignore[misc]
    """Draws, on every page, the running header and the footer "Página x de y" with the
    verification code. The total is only known at the end, so the pages are held until `save`."""

    def __init__(
        self, *args: Any, header: str, footer_code: str, footer_note: str, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._saved: list[dict[str, Any]] = []
        self._header = header
        self._footer_code = footer_code
        self._footer_note = footer_note

    def showPage(self) -> None:  # noqa: N802  (reportlab's name)
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._decorate(total)
            super().showPage()
        super().save()

    def _decorate(self, total: int) -> None:
        self.setStrokeColor(RULE)
        self.setLineWidth(0.5)
        self.setFont("Helvetica", 7)
        self.setFillColor(MUTED)
        self.drawString(MARGIN, PAGE_HEIGHT - 10 * mm, printable(self._header))
        self.line(MARGIN, PAGE_HEIGHT - 11.5 * mm, PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 11.5 * mm)
        self.line(MARGIN, 14 * mm, PAGE_WIDTH - MARGIN, 14 * mm)
        self.drawString(MARGIN, 10 * mm, printable(self._footer_code))
        self.drawRightString(PAGE_WIDTH - MARGIN, 10 * mm, f"Página {self._pageNumber} de {total}")
        self.drawString(MARGIN, 6.5 * mm, printable(self._footer_note))


def build_pdf(
    story: list[Flowable], *, title: str, header: str, footer_code: str, footer_note: str
) -> bytes:
    """The whole document as bytes. `invariant` makes the file depend only on its content (no clock
    and no random id of the library inside it), so the same data gives the same bytes."""
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title=printable(title),
        author="APM Digital",
        invariant=True,
    )
    document.build(
        story,
        canvasmaker=partial(
            NumberedCanvas,
            header=header,
            footer_code=footer_code,
            footer_note=footer_note,
        ),
    )
    return buffer.getvalue()
