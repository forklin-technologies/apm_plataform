"""How a number, a date or a name is written on the page (Brazilian conventions, school time zone).

Money is an integer number of cents; it is only ever split and printed here, never converted to a
float.
"""

import unicodedata
from datetime import date, datetime
from zoneinfo import ZoneInfo

MONTHS = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)  # fmt: skip


def brl(cents: int) -> str:
    """12345 -> 'R$ 123,45'; 123456789 -> 'R$ 1.234.567,89'; -150 -> '-R$ 1,50'."""
    whole, fraction = divmod(abs(cents), 100)
    grouped = f"{whole:,}".replace(",", ".")
    return f"{'-' if cents < 0 else ''}R$ {grouped},{fraction:02d}"


def percent(part: int, whole: int) -> str:
    """'12,3%' (one decimal, rounded half up, on integers); '—' when there is no whole."""
    if whole <= 0:
        return "—"
    tenths = (part * 1000 + whole // 2) // whole
    return f"{tenths // 10},{tenths % 10}%"


def reference(code: int) -> str:
    """The number the school sees: 42 -> 'APM-000042'."""
    return f"APM-{code:06d}"


def month_name(day: date) -> str:
    return f"{MONTHS[day.month - 1]} de {day.year}"


def local_datetime(moment: datetime, timezone: str) -> str:
    """'05/03/2025 09:00' in the time zone of the school."""
    return moment.astimezone(ZoneInfo(timezone)).strftime("%d/%m/%Y %H:%M")


def local_date(day: date) -> str:
    return day.strftime("%d/%m/%Y")


def initials(name: str | None) -> str:
    """'Maria Exemplo' -> 'M. E.': what a person without the right to see names gets."""
    words = (name or "").split()
    return " ".join(f"{word[0].upper()}." for word in words) if words else "—"


def short_id(identifier: str | None, size: int = 12) -> str:
    """The end-to-end id of a Pix, shortened: 'E1234567890…'."""
    if not identifier:
        return "—"
    return identifier if len(identifier) <= size else f"{identifier[:size]}…"


# Letters that have no decomposition into a base letter and an accent.
_STROKED = str.maketrans({"Ł": "L", "ł": "l", "Đ": "D", "đ": "d", "Ħ": "H", "ħ": "h", "ı": "i"})


def printable(value: object) -> str:
    """Text the standard PDF fonts can draw (Windows-1252, which has every Portuguese letter): a
    character outside it falls back to its base letter, or to '?'. A whole text is returned, so a
    name with an unusual letter is never dropped."""
    out: list[str] = []
    for char in str(value).translate(_STROKED):
        try:
            char.encode("cp1252")
            out.append(char)
        except UnicodeEncodeError:
            base = unicodedata.normalize("NFKD", char).encode("cp1252", "ignore").decode("cp1252")
            out.append(base or "?")
    return "".join(out)
