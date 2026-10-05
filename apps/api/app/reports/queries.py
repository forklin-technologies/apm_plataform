"""What the report routes read (constants given to `text()`, values bound)."""

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def names(db: Session, school_id: UUID) -> tuple[str, str]:
    """(name of the organization, name of the school): the APM and the school of the header."""
    row = db.execute(
        text(
            "SELECT o.name, s.name FROM schools s JOIN organizations o ON o.id = s.organization_id "
            "WHERE s.id = :school_id"
        ),
        {"school_id": school_id},
    ).one()
    return str(row[0]), str(row[1])


def category_names(db: Session, school_id: UUID) -> dict[str, str]:
    rows = db.execute(
        text("SELECT c.key, c.name FROM categories c WHERE c.school_id = :school_id"),
        {"school_id": school_id},
    )
    return {str(key): str(name) for key, name in rows}


def period_entries(db: Session, school_id: UUID, start: date, end: date) -> list[dict[str, Any]]:
    """Every cash entry of the period, from the database function that already computed them."""
    rows = db.execute(
        text("SELECT e.* FROM statement_entries(:school_id, :start, :end) e"),
        {"school_id": school_id, "start": start, "end": end},
    ).mappings()
    return [dict(row) for row in rows]


def pending_entries(db: Session, school_id: UUID) -> list[dict[str, Any]]:
    rows = db.execute(
        text("SELECT p.* FROM statement_pending(:school_id) p"), {"school_id": school_id}
    ).mappings()
    return [dict(row) for row in rows]


def set_report_ref(db: Session, closing_id: UUID, ref: str) -> None:
    """Record that the PDF exists. The column is set ONCE: a second writer matches no row."""
    db.execute(
        text(
            "UPDATE monthly_closings SET report_ref = :ref "
            "WHERE id = :closing_id AND report_ref IS NULL"
        ),
        {"ref": ref, "closing_id": closing_id},
    )
