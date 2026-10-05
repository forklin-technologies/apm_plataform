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
    """Record that the PDF exists. The column is set ONCE (a second writer matches no row) and only
    on an ACTIVE closing: a reopened one was superseded."""
    db.execute(
        text(
            "UPDATE monthly_closings SET report_ref = :ref "
            "WHERE id = :closing_id AND report_ref IS NULL AND reopened_at IS NULL"
        ),
        {"ref": ref, "closing_id": closing_id},
    )


def settled_contributions(
    db: Session, school_id: UUID, start: date, end: date
) -> list[dict[str, Any]]:
    """The contributions booked in the cash ledger in the period (the window of the statement: from
    00:00 of the first day to 00:00 after the last, in the time zone of the school), with what the
    detail rows know about them. One row per contribution: the Pix id is that of its paid charge,
    or the reference of a direct Pix."""
    rows = db.execute(
        text(
            "SELECT f.id AS transaction_id, f.reference_code, f.status, f.amount_cents, "
            "f.settled_at, f.occurred_at, f.late_adjustment, cat.key AS category_key, "
            "cat.name AS category_name, cat.report_group, c.method, c.guardian_name, "
            "c.student_name, c.class_name, "
            "coalesce(pc.end_to_end_id, c.external_reference) AS pix_id "
            "FROM school_settings st "
            "JOIN financial_transactions f ON f.school_id = st.school_id "
            "JOIN categories cat ON cat.id = f.category_id AND cat.school_id = st.school_id "
            "JOIN contributions c ON c.transaction_id = f.id AND c.school_id = st.school_id "
            "LEFT JOIN LATERAL (SELECT p.end_to_end_id FROM pix_charges p "
            "  WHERE p.transaction_id = f.id AND p.school_id = st.school_id "
            "  AND p.end_to_end_id IS NOT NULL "
            "  ORDER BY p.paid_at NULLS LAST, p.id LIMIT 1) pc ON true "
            "WHERE st.school_id = :school_id AND f.kind = 'CONTRIBUTION' "
            "AND f.settled_at >= CAST(:start AS date)::timestamp AT TIME ZONE st.timezone "
            "AND f.settled_at < (CAST(:end AS date) + 1)::timestamp AT TIME ZONE st.timezone "
            "ORDER BY f.settled_at, f.reference_code"
        ),
        {"school_id": school_id, "start": start, "end": end},
    ).mappings()
    return [dict(row) for row in rows]


def unsettled_contributions(
    db: Session, school_id: UUID, start: date, end: date
) -> list[dict[str, Any]]:
    """The contributions of the period (by the real date, `occurred_at`) that are NOT in the cash
    ledger: waiting for payment, in review, cancelled or expired. They never enter a total."""
    rows = db.execute(
        text(
            "SELECT f.id AS transaction_id, f.reference_code, f.status, f.amount_cents, "
            "f.occurred_at, f.late_adjustment, cat.key AS category_key, cat.name AS category_name, "
            "cat.report_group, c.method, c.guardian_name, c.student_name, c.class_name, "
            "coalesce(pc.end_to_end_id, c.external_reference) AS pix_id "
            "FROM school_settings st "
            "JOIN financial_transactions f ON f.school_id = st.school_id "
            "JOIN categories cat ON cat.id = f.category_id AND cat.school_id = st.school_id "
            "JOIN contributions c ON c.transaction_id = f.id AND c.school_id = st.school_id "
            "LEFT JOIN LATERAL (SELECT p.end_to_end_id FROM pix_charges p "
            "  WHERE p.transaction_id = f.id AND p.school_id = st.school_id "
            "  AND p.end_to_end_id IS NOT NULL "
            "  ORDER BY p.paid_at NULLS LAST, p.id LIMIT 1) pc ON true "
            "WHERE st.school_id = :school_id AND f.kind = 'CONTRIBUTION' AND f.settled_at IS NULL "
            "AND f.occurred_at >= CAST(:start AS date)::timestamp AT TIME ZONE st.timezone "
            "AND f.occurred_at < (CAST(:end AS date) + 1)::timestamp AT TIME ZONE st.timezone "
            "ORDER BY f.occurred_at, f.reference_code"
        ),
        {"school_id": school_id, "start": start, "end": end},
    ).mappings()
    return [dict(row) for row in rows]
