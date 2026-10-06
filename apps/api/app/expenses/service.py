"""The rules of the expenses module: who may do what, in which state, and what must be true first.

The database enforces the ledger (states, the edit window, partial approval, the reimbursement, the
audit); this layer decides what the database cannot know: WHO acts (the author edits, the author
never decides, only an approver approves) and answers with a stable `code`. Every rule the database
also has is checked here first, so a person gets a precise answer; `database_rules` is the net for
a race or a rule nobody foresaw.

Every function takes the `SchoolScope` of the request (organization, school and user come from the
session) and commits its own transaction.
"""

import base64
import binascii
import datetime as dt
import hashlib
import logging
import re
import unicodedata
import uuid
from collections.abc import Iterator
from typing import Any, BinaryIO, Protocol

from sqlalchemy.orm import Session

from app.auth.permissions import Permission
from app.core.errors import ProblemError
from app.expenses import repository
from app.expenses.access import SchoolScope, not_found
from app.expenses.attachment_types import HEAD_BYTES, sniff
from app.expenses.db_errors import database_rules
from app.expenses.schemas import (
    ApproveRequest,
    AttachmentOut,
    CategoryList,
    CategoryRef,
    ExpenseCreate,
    ExpenseDetail,
    ExpensePage,
    ExpenseSummary,
    ExpenseUpdate,
    ReimbursementOut,
)
from app.storage import AttachmentStore
from app.storage.base import CHUNK_SIZE

logger = logging.getLogger(__name__)

Row = repository.Row

# Where the "who spent it" of the ledger comes from: the role of the person who made the expense.
ORIGIN_BY_ROLE = {
    "staff": "TEACHER",
    "school_admin": "DIRECTOR",
    "organization_admin": "DIRECTOR",
    "treasurer": "MANAGEMENT",
}
DEFAULT_ORIGIN = "EMPLOYEE"
# Which category a reimbursement is filed under, by the origin of the expense.
REIMBURSEMENT_CATEGORY = {"DIRECTOR": "director_reimbursement"}
DEFAULT_REIMBURSEMENT_CATEGORY = "teacher_reimbursement"

MAX_ATTACHMENTS_PER_EXPENSE = 10
MAX_DAYS_AHEAD = 1  # an expense is not dated in the future (a day of slack for the time zones)
_PERIOD = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")
_CURSOR = re.compile(r"r[1-9][0-9]{0,17}")
_FILE_NAME_LENGTH = 200

SUMMARY_FIELDS = (
    "id",
    "reference_code",
    "status",
    "amount_cents",
    "approved_amount_cents",
    "occurred_at",
    "description",
    "vendor",
    "paid_by",
    "submitted_by_user_id",
    "attachments_count",
    "created_at",
    "updated_at",
)


# --- errors ---------------------------------------------------------------------------------------


def _invalid(field: str, code: str) -> ProblemError:
    """A 422 shaped like the validation errors of the framework: the field and a fixed code."""
    return ProblemError(
        422, "validation_error", "Invalid request", errors=[{"field": field, "code": code}]
    )


def invalid_field(field: str, code: str) -> ProblemError:
    """The same 422, for the router (a field of a form it parses itself)."""
    return _invalid(field, code)


def _invalid_state(row: Row) -> ProblemError:
    return ProblemError(
        409,
        "invalid_state",
        "The expense is in a state that does not allow this",
        detail=f"The expense is {row['status']}.",
    )


def _author_only() -> ProblemError:
    return ProblemError(403, "author_only", "Only the author of the expense can do this")


def _self_decision() -> ProblemError:
    return ProblemError(403, "self_approval_forbidden", "Nobody decides on their own expense")


# --- reading --------------------------------------------------------------------------------------


def _summary_data(row: Row) -> dict[str, Any]:
    data = {field: row[field] for field in SUMMARY_FIELDS}
    data["category"] = CategoryRef(
        id=row["category_id"], key=row["category_key"], name=row["category_name"]
    )
    return data


def _summary(row: Row) -> ExpenseSummary:
    return ExpenseSummary.model_validate(_summary_data(row))


def _detail(db: Session, scope: SchoolScope, row: Row) -> ExpenseDetail:
    attachments = [
        AttachmentOut.model_validate(dict(attachment))
        for attachment in repository.attachments_of(db, scope.school_id, row["id"])
    ]
    reimbursement = repository.reimbursement_of(db, scope.school_id, row["id"])
    return ExpenseDetail.model_validate(
        {
            **_summary_data(row),
            "purchase_reason": row["purchase_reason"],
            "payment_method": row["payment_method"],
            "approved_by_user_id": row["approved_by_user_id"],
            "approved_at": row["approved_at"],
            "decision_reason": row["decision_reason"],
            "correction_reason": row["correction_reason"],
            "settled_at": row["settled_at"],
            "attachments": attachments,
            "reimbursement": None
            if reimbursement is None
            else ReimbursementOut.model_validate(dict(reimbursement)),
        }
    )


def _fetch(db: Session, scope: SchoolScope, expense_id: uuid.UUID) -> Row:
    """The expense, if this person may see it. The answer for "no such expense", "another school's"
    and "somebody else's, and you read only your own" is the same 404."""
    rows = repository.search(db, scope.school_id, expense_id=expense_id, limit=1)
    if not rows:
        raise not_found()
    row = rows[0]
    if not scope.can_read_all and row["submitted_by_user_id"] != scope.user_id:
        raise not_found()
    return row


def _load_for_update(db: Session, scope: SchoolScope, expense_id: uuid.UUID) -> Row:
    """The same, but holding the lock of the row, and read again after waiting for it."""
    _fetch(db, scope, expense_id)
    if not repository.lock_expense(db, scope.school_id, expense_id):
        raise not_found()
    return _fetch(db, scope, expense_id)


def list_categories(db: Session, scope: SchoolScope) -> CategoryList:
    return CategoryList(
        items=[
            CategoryRef.model_validate(dict(row))
            for row in repository.categories_for_expenses(db, scope.school_id)
        ]
    )


def get_expense(db: Session, scope: SchoolScope, expense_id: uuid.UUID) -> ExpenseDetail:
    return _detail(db, scope, _fetch(db, scope, expense_id))


def _encode_cursor(reference_code: int) -> str:
    return base64.urlsafe_b64encode(f"r{reference_code}".encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> int | None:
    if cursor is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise _invalid("cursor", "invalid") from None
    if not _CURSOR.fullmatch(raw):
        raise _invalid("cursor", "invalid")
    return int(raw[1:])


def _period_start(period: str | None) -> dt.date | None:
    if period is None:
        return None
    found = _PERIOD.fullmatch(period)
    if found is None:
        raise _invalid("period", "invalid")
    try:
        return dt.date(int(found.group(1)), int(found.group(2)), 1)
    except ValueError:
        raise _invalid("period", "invalid") from None


def list_expenses(
    db: Session,
    scope: SchoolScope,
    *,
    status: str | None,
    period: str | None,
    cursor: str | None,
    limit: int,
) -> ExpensePage:
    """Newest first. Whoever has `expenses:read_all` sees every expense of the school; everybody
    else sees the ones they wrote. `period` is a month (YYYY-MM) of the date of the expense, in the
    time zone of the school."""
    rows = repository.search(
        db,
        scope.school_id,
        author_id=None if scope.can_read_all else scope.user_id,
        status=status,
        period_start=_period_start(period),
        before=_decode_cursor(cursor),
        limit=limit + 1,
    )
    page = rows[:limit]
    more = len(rows) > limit
    return ExpensePage(
        items=[_summary(row) for row in page],
        next_cursor=_encode_cursor(page[-1]["reference_code"]) if more else None,
    )


# --- the request: create and edit -----------------------------------------------------------------


def _check_category(db: Session, scope: SchoolScope, category_id: uuid.UUID) -> None:
    allowed = {row["id"] for row in repository.categories_for_expenses(db, scope.school_id)}
    if category_id not in allowed:
        # Unknown, another school's, money in, inactive, or without approval: one answer.
        raise _invalid("category_id", "not_available")


def _resolve_occurred_at(
    db: Session, scope: SchoolScope, value: dt.datetime | dt.date
) -> dt.datetime:
    instant = value if isinstance(value, dt.datetime) else None
    day = None if isinstance(value, dt.datetime) else value
    resolved = repository.resolve_occurred_at(db, scope.school_id, instant=instant, day=day)
    if resolved > dt.datetime.now(dt.UTC) + dt.timedelta(days=MAX_DAYS_AHEAD):
        raise _invalid("occurred_at", "in_the_future")
    return resolved


def _audit(db: Session, scope: SchoolScope, row: Row, action: str, **data: Any) -> None:
    repository.audit_event(
        db,
        organization_id=scope.organization_id,
        school_id=scope.school_id,
        action=action,
        transaction_id=row["id"],
        reference_code=row["reference_code"],
        data=data,
    )


def create_expense(db: Session, scope: SchoolScope, body: ExpenseCreate) -> ExpenseDetail:
    """A DRAFT. The actor and the school come from the session; the person who writes it is its
    author (the one who may edit and send it, and who can never decide on it)."""
    _check_category(db, scope, body.category_id)
    occurred_at = _resolve_occurred_at(db, scope, body.occurred_at)
    with database_rules(db):
        expense_id = repository.insert_transaction(
            db,
            organization_id=scope.organization_id,
            school_id=scope.school_id,
            kind="EXPENSE",
            amount_cents=body.amount_cents,
            status="DRAFT",
            category_id=body.category_id,
            origin_type=ORIGIN_BY_ROLE.get(scope.role, DEFAULT_ORIGIN),
            origin_user_id=scope.user_id,
            occurred_at=occurred_at,
            created_by_user_id=scope.user_id,
        )
        repository.insert_expense_detail(
            db,
            transaction_id=expense_id,
            organization_id=scope.organization_id,
            school_id=scope.school_id,
            description=body.description,
            vendor=body.vendor,
            purchase_reason=body.purchase_reason,
            payment_method=body.payment_method,
            paid_by=body.paid_by,
            submitted_by_user_id=scope.user_id,
        )
        row = _fetch(db, scope, expense_id)
        _audit(db, scope, row, "expense.created", status="DRAFT", amount_cents=body.amount_cents)
        db.commit()
    return get_expense(db, scope, expense_id)


def update_expense(
    db: Session, scope: SchoolScope, expense_id: uuid.UUID, body: ExpenseUpdate
) -> ExpenseDetail:
    """Only the author, and only while the expense is a DRAFT or waiting for a correction."""
    sent = body.model_fields_set
    if not sent:
        raise _invalid("body", "empty")
    for field in ("amount_cents", "occurred_at", "category_id", "description"):
        if field in sent and getattr(body, field) is None:
            raise _invalid(field, "null_not_allowed")
    with database_rules(db):
        row = _load_for_update(db, scope, expense_id)
        if row["submitted_by_user_id"] != scope.user_id:
            raise _author_only()
        if row["status"] not in repository.EDITABLE_STATUSES:
            raise _invalid_state(row)
        transaction_values: dict[str, Any] = {}
        expense_values: dict[str, Any] = {}
        if "amount_cents" in sent:
            transaction_values["amount_cents"] = body.amount_cents
        if "category_id" in sent and body.category_id is not None:
            _check_category(db, scope, body.category_id)
            transaction_values["category_id"] = body.category_id
        if "occurred_at" in sent and body.occurred_at is not None:
            transaction_values["occurred_at"] = _resolve_occurred_at(db, scope, body.occurred_at)
        for field in ("description", "vendor", "purchase_reason", "payment_method"):
            if field in sent:
                expense_values[field] = getattr(body, field)
        if not repository.update_request(
            db,
            scope.school_id,
            expense_id,
            transaction_values=transaction_values,
            expense_values=expense_values,
        ):
            raise _invalid_state(row)
        _audit(db, scope, row, "expense.updated", fields=sorted(sent))  # names, never the values
        db.commit()
    return get_expense(db, scope, expense_id)


# --- attachments ----------------------------------------------------------------------------------


class _TooLarge(Exception):
    pass


def clean_file_name(raw: str | None, extension: str) -> str:
    """A display name from what the client called the file: no path, no control characters, a
    bounded length. It is only a label (the file lives under a key made by the server)."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = name.encode("utf-8", "ignore").decode("utf-8")  # drops a lone surrogate
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    name = name[:_FILE_NAME_LENGTH].strip(" .")
    return name or f"anexo{extension}"


class _Hasher(Protocol):
    def update(self, data: bytes, /) -> None: ...


def _limited_chunks(first: bytes, stream: BinaryIO, hasher: _Hasher, limit: int) -> Iterator[bytes]:
    total = 0
    chunk = first
    while chunk:
        total += len(chunk)
        if total > limit:
            raise _TooLarge
        hasher.update(chunk)
        yield chunk
        chunk = stream.read(CHUNK_SIZE)


def _discard(store: AttachmentStore, key: str) -> None:
    """Remove a file that no row will point to. It runs while another error is being raised, so it
    must never raise itself (it would replace the error the caller needs to see): a failure is
    logged, with the class of the error and never the key or a path, and the file is left behind."""
    try:
        store.delete(key)
    except Exception as error:
        logger.error(
            "could not remove an attachment file after a failed write",
            extra={"error_class": type(error).__name__},
        )


def add_attachment(
    db: Session,
    scope: SchoolScope,
    store: AttachmentStore,
    expense_id: uuid.UUID,
    *,
    kind: str,
    file_name: str | None,
    stream: BinaryIO,
    max_bytes: int,
) -> AttachmentOut:
    """Only the author, and only while the expense is a DRAFT or waiting for a correction: after it
    was sent, the evidence is what the approver saw (the database would still allow more until the
    expense is final).

    The type is read from the bytes (an image or a PDF), the size is bounded while streaming, and
    the sha256 is computed on the way to the store. The bytes are stored first and the row after.
    If writing the row fails, the stored file is removed. If the COMMIT fails the file is kept:
    the commit may have reached the server and only the answer got lost, and a row without its
    file is worse than a file without a row (an orphan, which a clean-up job can find).
    """
    with database_rules(db):
        row = _load_for_update(db, scope, expense_id)
        if row["submitted_by_user_id"] != scope.user_id:
            raise _author_only()
        if row["status"] not in repository.EDITABLE_STATUSES:
            # Once it was sent, what the approver looked at is what gets approved and paid.
            raise ProblemError(
                409,
                "attachments_closed",
                "Files can only be attached while the expense is a draft or being corrected",
                detail=f"The expense is {row['status']}.",
            )
        if row["attachments_count"] >= MAX_ATTACHMENTS_PER_EXPENSE:
            raise ProblemError(409, "attachment_limit", "The expense has too many attachments")
        head = stream.read(max(CHUNK_SIZE, HEAD_BYTES))
        if not head:
            raise _invalid("file", "empty")
        attachment_type = sniff(head)
        if attachment_type is None:
            raise ProblemError(
                415, "attachment_type_not_allowed", "Only images (PNG, JPEG, WebP) and PDF files"
            )
        key = f"{scope.organization_id}/{scope.school_id}/{expense_id}/{uuid.uuid4().hex}"
        hasher = hashlib.sha256()
        try:
            size = store.put(key, _limited_chunks(head, stream, hasher, max_bytes))
        except _TooLarge:
            raise ProblemError(
                413, "payload_too_large", "The file is larger than the limit"
            ) from None
        try:
            created = repository.insert_attachment(
                db,
                organization_id=scope.organization_id,
                school_id=scope.school_id,
                expense_id=expense_id,
                kind=kind,
                storage_key=key,
                file_name=clean_file_name(file_name, attachment_type.extension),
                content_type=attachment_type.content_type,
                size_bytes=size,
                sha256=hasher.hexdigest(),
                uploaded_by_user_id=scope.user_id,
            )
            _audit(
                db,
                scope,
                row,
                "expense.attachment_added",
                attachment_id=str(created["id"]),
                kind=kind,
                size_bytes=size,
            )
        except BaseException:
            _discard(store, key)
            raise
        db.commit()  # a failure HERE keeps the file (see the docstring)
    return AttachmentOut.model_validate(dict(created))


def attachment_for_download(
    db: Session, scope: SchoolScope, expense_id: uuid.UUID, attachment_id: uuid.UUID
) -> Row:
    """The attachment, for whoever can see its expense; anyone else gets the 404."""
    _fetch(db, scope, expense_id)
    attachment = repository.get_attachment(db, scope.school_id, expense_id, attachment_id)
    if attachment is None:
        raise not_found()
    return attachment


# --- the actions ----------------------------------------------------------------------------------


def _move(db: Session, scope: SchoolScope, row: Row, new: str) -> None:
    if not repository.set_status(db, scope.school_id, row["id"], expected=row["status"], new=new):
        raise _invalid_state(row)  # somebody else moved it between our read and our write


def _require_status(row: Row, *allowed: str) -> None:
    if row["status"] not in allowed:
        raise _invalid_state(row)


def submit(db: Session, scope: SchoolScope, expense_id: uuid.UUID) -> ExpenseDetail:
    """DRAFT or CORRECTION_REQUESTED to SUBMITTED. The author only. Needs at least one attachment,
    the reason of the purchase and how it was paid (the database checks it again at commit)."""
    with database_rules(db):
        row = _load_for_update(db, scope, expense_id)
        if row["submitted_by_user_id"] != scope.user_id:
            raise _author_only()
        _require_status(row, *repository.EDITABLE_STATUSES)
        missing = []
        if not row["purchase_reason"]:
            missing.append({"field": "purchase_reason", "code": "required"})
        if not row["payment_method"]:
            missing.append({"field": "payment_method", "code": "required"})
        if row["attachments_count"] < 1:
            missing.append({"field": "attachments", "code": "required"})
        if missing:
            raise ProblemError(
                422, "expense_incomplete", "The expense is not ready to be sent", errors=missing
            )
        _move(db, scope, row, "SUBMITTED")
        _audit(db, scope, row, "expense.submitted", status="SUBMITTED")
        db.commit()
    return get_expense(db, scope, expense_id)


def approve(
    db: Session, scope: SchoolScope, expense_id: uuid.UUID, body: ApproveRequest
) -> ExpenseDetail:
    """SUBMITTED to APPROVED, never by the author. The approved amount is the requested one unless
    it is lower AND a collaborator paid (an expense the APM paid is approved in full)."""
    with database_rules(db):
        row = _load_for_update(db, scope, expense_id)
        if row["submitted_by_user_id"] == scope.user_id:
            raise _self_decision()
        _require_status(row, "SUBMITTED")
        requested: int = row["amount_cents"]
        approved = requested if body.approved_amount_cents is None else body.approved_amount_cents
        if approved > requested:
            raise _invalid("approved_amount_cents", "above_requested")
        if approved < requested and row["paid_by"] != "COLLABORATOR":
            raise ProblemError(
                422,
                "partial_approval_not_allowed",
                "Only an expense paid by a collaborator can be approved for less",
                errors=[{"field": "approved_amount_cents", "code": "paid_by_apm"}],
            )
        repository.record_decision(
            db,
            scope.school_id,
            expense_id,
            decided_by=scope.user_id,
            approved_amount_cents=approved,
            reason=body.reason,
        )
        _move(db, scope, row, "APPROVED")
        _audit(
            db,
            scope,
            row,
            "expense.approved",
            status="APPROVED",
            amount_cents=requested,
            approved_amount_cents=approved,
        )
        db.commit()
    return get_expense(db, scope, expense_id)


def reject(db: Session, scope: SchoolScope, expense_id: uuid.UUID, reason: str) -> ExpenseDetail:
    """SUBMITTED to REJECTED (final), never by the author, with the reason."""
    with database_rules(db):
        row = _load_for_update(db, scope, expense_id)
        if row["submitted_by_user_id"] == scope.user_id:
            raise _self_decision()
        _require_status(row, "SUBMITTED")
        repository.record_decision(
            db,
            scope.school_id,
            expense_id,
            decided_by=scope.user_id,
            approved_amount_cents=None,
            reason=reason,
        )
        _move(db, scope, row, "REJECTED")
        _audit(db, scope, row, "expense.rejected", status="REJECTED")
        db.commit()
    return get_expense(db, scope, expense_id)


def request_correction(
    db: Session, scope: SchoolScope, expense_id: uuid.UUID, reason: str
) -> ExpenseDetail:
    """SUBMITTED back to the author (CORRECTION_REQUESTED), who may edit and send it again. Like a
    decision, never by the author."""
    with database_rules(db):
        row = _load_for_update(db, scope, expense_id)
        if row["submitted_by_user_id"] == scope.user_id:
            raise _self_decision()
        _require_status(row, "SUBMITTED")
        repository.record_correction_reason(db, scope.school_id, expense_id, reason)
        _move(db, scope, row, "CORRECTION_REQUESTED")
        _audit(db, scope, row, "expense.correction_requested", status="CORRECTION_REQUESTED")
        db.commit()
    return get_expense(db, scope, expense_id)


def cancel(db: Session, scope: SchoolScope, expense_id: uuid.UUID) -> ExpenseDetail:
    """CANCELLED (final). The author cancels a DRAFT or an expense waiting for a correction; an
    approver cancels an APPROVED one (the database refuses it while a reimbursement is live)."""
    with database_rules(db):
        row = _load_for_update(db, scope, expense_id)
        status = row["status"]
        if status in repository.EDITABLE_STATUSES:
            if row["submitted_by_user_id"] != scope.user_id:
                raise _author_only()
        elif status == "APPROVED":
            if Permission.EXPENSES_APPROVE not in scope.permissions:
                raise ProblemError(403, "permission_denied", "You are not allowed to do this")
        else:
            raise _invalid_state(row)
        _move(db, scope, row, "CANCELLED")
        _audit(db, scope, row, "expense.cancelled", status="CANCELLED", previous_status=status)
        db.commit()
    return get_expense(db, scope, expense_id)


def reimburse(
    db: Session, scope: SchoolScope, expense_id: uuid.UUID, payment_reference: str
) -> ExpenseDetail:
    """Record the reimbursement of an APPROVED expense that a collaborator paid, for the approved
    amount, and move the expense to PAID. The platform moves no money: the treasury already paid in
    the bank and this registers it. Not by the person being reimbursed."""
    with database_rules(db):
        row = _load_for_update(db, scope, expense_id)
        _require_status(row, "APPROVED")
        if row["paid_by"] != "COLLABORATOR":
            raise ProblemError(
                409,
                "reimbursement_not_applicable",
                "Only an expense paid by a collaborator is reimbursed",
            )
        beneficiary: uuid.UUID = row["submitted_by_user_id"]
        if beneficiary == scope.user_id:
            raise ProblemError(
                403, "self_reimbursement_forbidden", "Nobody registers their own reimbursement"
            )
        approved: int | None = row["approved_amount_cents"]
        if approved is None:  # the database guarantees one for an APPROVED expense
            raise _invalid_state(row)
        category_key = REIMBURSEMENT_CATEGORY.get(
            row["origin_type"], DEFAULT_REIMBURSEMENT_CATEGORY
        )
        category_id = repository.reimbursement_category(db, scope.school_id, category_key)
        if category_id is None:
            raise ProblemError(
                409,
                "reimbursement_category_missing",
                "The school has no active category for reimbursements",
            )
        reimbursement_id = repository.insert_transaction(
            db,
            organization_id=scope.organization_id,
            school_id=scope.school_id,
            kind="REIMBURSEMENT",
            amount_cents=approved,
            status="PENDING",
            category_id=category_id,
            origin_type=row["origin_type"],
            origin_user_id=beneficiary,
            occurred_at=None,
            created_by_user_id=scope.user_id,
            parent_id=expense_id,
        )
        repository.insert_reimbursement_detail(
            db,
            transaction_id=reimbursement_id,
            organization_id=scope.organization_id,
            school_id=scope.school_id,
            beneficiary_user_id=beneficiary,
        )
        repository.record_payment(
            db,
            scope.school_id,
            reimbursement_id,
            paid_by_user_id=scope.user_id,
            payment_reference=payment_reference,
        )
        # The cash moves with the reimbursement; the expense it pays goes to PAID with it.
        if not repository.settle(db, scope.school_id, reimbursement_id, expected="PENDING"):
            raise _invalid_state(row)
        if not repository.settle(db, scope.school_id, expense_id, expected="APPROVED"):
            raise _invalid_state(row)
        _audit(
            db,
            scope,
            row,
            "expense.reimbursed",
            status="PAID",
            reimbursement_id=str(reimbursement_id),
            approved_amount_cents=approved,
        )
        db.commit()
    return get_expense(db, scope, expense_id)


def pay(db: Session, scope: SchoolScope, expense_id: uuid.UUID) -> ExpenseDetail:
    """APPROVED to PAID for an expense the APM paid itself (a collaborator's expense is paid by its
    reimbursement). It records a payment already made; the cash entry is booked now. Not by the
    author of the expense, as for a reimbursement."""
    with database_rules(db):
        row = _load_for_update(db, scope, expense_id)
        _require_status(row, "APPROVED")
        if row["paid_by"] != "APM":
            raise ProblemError(
                409,
                "payment_by_reimbursement",
                "An expense paid by a collaborator is paid by its reimbursement",
            )
        if row["submitted_by_user_id"] == scope.user_id:
            raise ProblemError(
                403, "self_payment_forbidden", "Nobody registers the payment of their own expense"
            )
        if not repository.settle(db, scope.school_id, expense_id, expected="APPROVED"):
            raise _invalid_state(row)
        _audit(db, scope, row, "expense.paid", status="PAID")
        db.commit()
    return get_expense(db, scope, expense_id)
