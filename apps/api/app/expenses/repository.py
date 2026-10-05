"""The SQL of the expenses module: constant statements, values always bound.

Every statement names the school (`school_id`) even though row level security already limits what
the session sees: an organization-wide membership sees every school of its organization, and the
school in the path is the one the caller means. Nothing here decides who may do what (the service
does) or what the ledger allows (the database does).
"""

import datetime as dt
import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import RowMapping, func, text, update
from sqlalchemy.orm import Session

from app.models.financial import Expense, FinancialTransaction

Row = RowMapping

EDITABLE_STATUSES = ("DRAFT", "CORRECTION_REQUESTED")


def categories_for_expenses(db: Session, school_id: uuid.UUID) -> Sequence[Row]:
    """The categories an expense can be filed under: money OUT, active, and that need approval (the
    bank fees are recorded by the treasury, born approved, and never go through this form)."""
    return (
        db.execute(
            text(
                "SELECT id, key, name FROM categories "
                "WHERE school_id = :school_id AND applies_to = 'OUT' AND is_active "
                "AND requires_approval ORDER BY name, key"
            ),
            {"school_id": school_id},
        )
        .mappings()
        .all()
    )


def reimbursement_category(db: Session, school_id: uuid.UUID, key: str) -> uuid.UUID | None:
    found = db.execute(
        text(
            "SELECT id FROM categories "
            "WHERE school_id = :school_id AND key = :key AND applies_to = 'OUT' AND is_active"
        ),
        {"school_id": school_id, "key": key},
    ).scalar_one_or_none()
    return None if found is None else uuid.UUID(str(found))


def resolve_occurred_at(
    db: Session, school_id: uuid.UUID, *, instant: dt.datetime | None, day: dt.date | None
) -> dt.datetime:
    """The instant itself, or noon of `day` in the time zone of the school."""
    value: dt.datetime = db.execute(
        text(
            "SELECT coalesce(CAST(:instant AS timestamptz), "
            "(CAST(:day AS date) + time '12:00') AT TIME ZONE timezone) "
            "FROM school_settings WHERE school_id = :school_id"
        ),
        {"instant": instant, "day": day, "school_id": school_id},
    ).scalar_one()
    return value


def insert_transaction(
    db: Session,
    *,
    organization_id: uuid.UUID,
    school_id: uuid.UUID,
    kind: str,
    amount_cents: int,
    status: str,
    category_id: uuid.UUID,
    origin_type: str,
    origin_user_id: uuid.UUID,
    occurred_at: dt.datetime | None,
    created_by_user_id: uuid.UUID,
    parent_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """A ledger row of money OUT: an EXPENSE (no parent) or a REIMBURSEMENT (of an expense)."""
    created: uuid.UUID = db.execute(
        text(
            "INSERT INTO financial_transactions (organization_id, school_id, kind, direction, "
            "amount_cents, status, category_id, origin_type, origin_user_id, occurred_at, "
            "created_by_user_id, parent_transaction_id, parent_kind) "
            "VALUES (:organization_id, :school_id, :kind, 'OUT', :amount_cents, :status, "
            ":category_id, :origin_type, :origin_user_id, "
            "coalesce(CAST(:occurred_at AS timestamptz), now()), :created_by_user_id, "
            "CAST(:parent_id AS uuid), CASE WHEN CAST(:parent_id AS uuid) IS NULL THEN NULL "
            "ELSE 'EXPENSE' END) RETURNING id"
        ),
        {
            "organization_id": organization_id,
            "school_id": school_id,
            "kind": kind,
            "amount_cents": amount_cents,
            "status": status,
            "category_id": category_id,
            "origin_type": origin_type,
            "origin_user_id": origin_user_id,
            "occurred_at": occurred_at,
            "created_by_user_id": created_by_user_id,
            "parent_id": parent_id,
        },
    ).scalar_one()
    return created


def insert_expense_detail(
    db: Session,
    *,
    transaction_id: uuid.UUID,
    organization_id: uuid.UUID,
    school_id: uuid.UUID,
    description: str,
    vendor: str | None,
    purchase_reason: str | None,
    payment_method: str | None,
    paid_by: str,
    submitted_by_user_id: uuid.UUID,
) -> None:
    db.execute(
        text(
            "INSERT INTO expenses (transaction_id, organization_id, school_id, description, "
            "vendor, purchase_reason, payment_method, paid_by, submitted_by_user_id) "
            "VALUES (:transaction_id, :organization_id, :school_id, :description, :vendor, "
            ":purchase_reason, :payment_method, :paid_by, :submitted_by_user_id)"
        ),
        {
            "transaction_id": transaction_id,
            "organization_id": organization_id,
            "school_id": school_id,
            "description": description,
            "vendor": vendor,
            "purchase_reason": purchase_reason,
            "payment_method": payment_method,
            "paid_by": paid_by,
            "submitted_by_user_id": submitted_by_user_id,
        },
    )


def search(
    db: Session,
    school_id: uuid.UUID,
    *,
    expense_id: uuid.UUID | None = None,
    author_id: uuid.UUID | None = None,
    status: str | None = None,
    period_start: dt.date | None = None,
    before: int | None = None,
    limit: int = 1,
) -> Sequence[Row]:
    """Expenses of the school, newest first (by reference code). One statement serves the list and
    the detail: a filter that is NULL does not apply."""
    return (
        db.execute(
            text(
                "SELECT ft.id, ft.reference_code, ft.status, ft.amount_cents, ft.occurred_at, "
                "ft.settled_at, ft.created_at, ft.updated_at, ft.origin_type, "
                "c.id AS category_id, c.key AS category_key, c.name AS category_name, "
                "e.description, e.vendor, e.purchase_reason, e.payment_method, e.paid_by, "
                "e.submitted_by_user_id, e.approved_by_user_id, e.approved_at, "
                "e.approved_amount_cents, e.decision_reason, e.correction_reason, "
                "(SELECT count(*) FROM expense_attachments a "
                " WHERE a.transaction_id = ft.id AND a.school_id = ft.school_id) "
                "AS attachments_count "
                "FROM financial_transactions ft "
                "JOIN expenses e ON e.transaction_id = ft.id "
                "AND e.organization_id = ft.organization_id AND e.school_id = ft.school_id "
                "JOIN categories c ON c.id = ft.category_id "
                "AND c.organization_id = ft.organization_id AND c.school_id = ft.school_id "
                "LEFT JOIN school_settings ss ON ss.school_id = ft.school_id "
                "WHERE ft.kind = 'EXPENSE' AND ft.school_id = :school_id "
                "AND (CAST(:expense_id AS uuid) IS NULL OR ft.id = :expense_id) "
                "AND (CAST(:author_id AS uuid) IS NULL OR e.submitted_by_user_id = :author_id) "
                "AND (CAST(:status AS text) IS NULL OR ft.status = :status) "
                "AND (CAST(:period_start AS date) IS NULL OR ("
                "ft.occurred_at >= CAST(CAST(:period_start AS date) AS timestamp) "
                "AT TIME ZONE ss.timezone "
                "AND ft.occurred_at < (CAST(:period_start AS date) + interval '1 month') "
                "AT TIME ZONE ss.timezone)) "
                "AND (CAST(:before AS bigint) IS NULL OR ft.reference_code < :before) "
                "ORDER BY ft.reference_code DESC LIMIT :limit"
            ),
            {
                "school_id": school_id,
                "expense_id": expense_id,
                "author_id": author_id,
                "status": status,
                "period_start": period_start,
                "before": before,
                "limit": limit,
            },
        )
        .mappings()
        .all()
    )


def lock_expense(db: Session, school_id: uuid.UUID, expense_id: uuid.UUID) -> bool:
    """Take the row lock of the expense (waits for whoever holds it); False when there is none."""
    found = db.execute(
        text(
            "SELECT 1 FROM financial_transactions "
            "WHERE id = :id AND school_id = :school_id AND kind = 'EXPENSE' FOR UPDATE"
        ),
        {"id": expense_id, "school_id": school_id},
    ).first()
    return found is not None


def attachments_of(db: Session, school_id: uuid.UUID, expense_id: uuid.UUID) -> Sequence[Row]:
    return (
        db.execute(
            text(
                "SELECT id, kind, file_name, content_type, size_bytes, sha256, "
                "uploaded_by_user_id, created_at FROM expense_attachments "
                "WHERE school_id = :school_id AND transaction_id = :expense_id "
                "ORDER BY created_at, id"
            ),
            {"school_id": school_id, "expense_id": expense_id},
        )
        .mappings()
        .all()
    )


def get_attachment(
    db: Session, school_id: uuid.UUID, expense_id: uuid.UUID, attachment_id: uuid.UUID
) -> Row | None:
    return (
        db.execute(
            text(
                "SELECT id, kind, storage_key, file_name, content_type, size_bytes, sha256 "
                "FROM expense_attachments WHERE id = :id AND school_id = :school_id "
                "AND transaction_id = :expense_id"
            ),
            {"id": attachment_id, "school_id": school_id, "expense_id": expense_id},
        )
        .mappings()
        .one_or_none()
    )


def insert_attachment(
    db: Session,
    *,
    organization_id: uuid.UUID,
    school_id: uuid.UUID,
    expense_id: uuid.UUID,
    kind: str,
    storage_key: str,
    file_name: str,
    content_type: str,
    size_bytes: int,
    sha256: str,
    uploaded_by_user_id: uuid.UUID,
) -> Row:
    return (
        db.execute(
            text(
                "INSERT INTO expense_attachments (organization_id, school_id, transaction_id, "
                "kind, storage_key, file_name, content_type, size_bytes, sha256, "
                "uploaded_by_user_id) VALUES (:organization_id, :school_id, :expense_id, :kind, "
                ":storage_key, :file_name, :content_type, :size_bytes, :sha256, "
                ":uploaded_by_user_id) RETURNING id, kind, file_name, content_type, size_bytes, "
                "sha256, uploaded_by_user_id, created_at"
            ),
            {
                "organization_id": organization_id,
                "school_id": school_id,
                "expense_id": expense_id,
                "kind": kind,
                "storage_key": storage_key,
                "file_name": file_name,
                "content_type": content_type,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "uploaded_by_user_id": uploaded_by_user_id,
            },
        )
        .mappings()
        .one()
    )


def update_request(
    db: Session,
    school_id: uuid.UUID,
    expense_id: uuid.UUID,
    *,
    transaction_values: Mapping[str, Any],
    expense_values: Mapping[str, Any],
) -> bool:
    """Change the request of an expense that is still editable. False: it no longer is."""
    changed = db.execute(
        update(FinancialTransaction)
        .where(
            FinancialTransaction.id == expense_id,
            FinancialTransaction.school_id == school_id,
            FinancialTransaction.kind == "EXPENSE",
            FinancialTransaction.status.in_(EDITABLE_STATUSES),
        )
        .values(**transaction_values, updated_at=func.now())
        .returning(FinancialTransaction.id)
        .execution_options(synchronize_session=False)
    ).first()
    if changed is None:
        return False
    if expense_values:
        db.execute(
            update(Expense)
            .where(Expense.transaction_id == expense_id, Expense.school_id == school_id)
            .values(**expense_values, updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
    return True


def set_status(
    db: Session, school_id: uuid.UUID, transaction_id: uuid.UUID, *, expected: str, new: str
) -> bool:
    """Compare and swap: move the row from `expected` to `new`; False when it was not in `expected`
    (someone else got there first). The database refuses an edge that does not exist."""
    moved = db.execute(
        text(
            "UPDATE financial_transactions SET status = :new, updated_at = now() "
            "WHERE id = :id AND school_id = :school_id AND status = :expected RETURNING id"
        ),
        {"id": transaction_id, "school_id": school_id, "expected": expected, "new": new},
    ).first()
    return moved is not None


def settle(db: Session, school_id: uuid.UUID, transaction_id: uuid.UUID, *, expected: str) -> bool:
    """Move the row to PAID, booked now (the database may book a late one in the open period)."""
    settled = db.execute(
        text(
            "UPDATE financial_transactions SET status = 'PAID', settled_at = clock_timestamp(), "
            "updated_at = now() WHERE id = :id AND school_id = :school_id AND status = :expected "
            "RETURNING id"
        ),
        {"id": transaction_id, "school_id": school_id, "expected": expected},
    ).first()
    return settled is not None


def record_decision(
    db: Session,
    school_id: uuid.UUID,
    expense_id: uuid.UUID,
    *,
    decided_by: uuid.UUID,
    approved_amount_cents: int | None,
    reason: str | None,
) -> None:
    """Who decided, the approved amount (none for a rejection) and why. Written once."""
    db.execute(
        text(
            "UPDATE expenses SET approved_by_user_id = :decided_by, "
            "approved_amount_cents = :approved, decision_reason = :reason, updated_at = now() "
            "WHERE transaction_id = :id AND school_id = :school_id"
        ),
        {
            "decided_by": decided_by,
            "approved": approved_amount_cents,
            "reason": reason,
            "id": expense_id,
            "school_id": school_id,
        },
    )


def record_correction_reason(
    db: Session, school_id: uuid.UUID, expense_id: uuid.UUID, reason: str
) -> None:
    db.execute(
        text(
            "UPDATE expenses SET correction_reason = :reason, updated_at = now() "
            "WHERE transaction_id = :id AND school_id = :school_id"
        ),
        {"reason": reason, "id": expense_id, "school_id": school_id},
    )


def insert_reimbursement_detail(
    db: Session,
    *,
    transaction_id: uuid.UUID,
    organization_id: uuid.UUID,
    school_id: uuid.UUID,
    beneficiary_user_id: uuid.UUID,
) -> None:
    db.execute(
        text(
            "INSERT INTO reimbursements (transaction_id, organization_id, school_id, "
            "beneficiary_user_id) VALUES (:transaction_id, :organization_id, :school_id, "
            ":beneficiary_user_id)"
        ),
        {
            "transaction_id": transaction_id,
            "organization_id": organization_id,
            "school_id": school_id,
            "beneficiary_user_id": beneficiary_user_id,
        },
    )


def record_payment(
    db: Session,
    school_id: uuid.UUID,
    reimbursement_id: uuid.UUID,
    *,
    paid_by_user_id: uuid.UUID,
    payment_reference: str,
) -> None:
    """The payment the treasury made in the bank (the platform moves no money). Written once."""
    db.execute(
        text(
            "UPDATE reimbursements SET paid_by_user_id = :paid_by, "
            "payment_reference = :reference, updated_at = now() "
            "WHERE transaction_id = :id AND school_id = :school_id"
        ),
        {
            "paid_by": paid_by_user_id,
            "reference": payment_reference,
            "id": reimbursement_id,
            "school_id": school_id,
        },
    )


def reimbursement_of(db: Session, school_id: uuid.UUID, expense_id: uuid.UUID) -> Row | None:
    """The live reimbursement of an expense (the last one when all were cancelled)."""
    return (
        db.execute(
            text(
                "SELECT ft.id, ft.status, ft.amount_cents, ft.settled_at, ft.created_at, "
                "r.beneficiary_user_id, r.paid_by_user_id, r.payment_reference "
                "FROM financial_transactions ft JOIN reimbursements r "
                "ON r.transaction_id = ft.id AND r.organization_id = ft.organization_id "
                "AND r.school_id = ft.school_id "
                "WHERE ft.kind = 'REIMBURSEMENT' AND ft.school_id = :school_id "
                "AND ft.parent_transaction_id = :expense_id "
                "ORDER BY (ft.status = 'CANCELLED'), ft.created_at DESC LIMIT 1"
            ),
            {"school_id": school_id, "expense_id": expense_id},
        )
        .mappings()
        .one_or_none()
    )


def audit_event(
    db: Session,
    *,
    organization_id: uuid.UUID,
    school_id: uuid.UUID,
    action: str,
    transaction_id: uuid.UUID,
    reference_code: int,
    data: Mapping[str, Any],
) -> None:
    """A high-level event (`expense.approved`) next to the row changes the triggers record. The
    actor is NOT passed: the database takes it from the settings of the transaction. `data` holds
    ids, amounts and statuses only, never free text."""
    db.execute(
        text(
            "INSERT INTO audit_logs (organization_id, school_id, action, entity_type, entity_id, "
            "entity_reference, after_data) VALUES (:organization_id, :school_id, :action, "
            "'financial_transactions', :entity_id, :reference, CAST(:data AS jsonb))"
        ),
        {
            "organization_id": organization_id,
            "school_id": school_id,
            "action": action,
            "entity_id": transaction_id,
            "reference": reference_code,
            "data": json.dumps(data),
        },
    )
