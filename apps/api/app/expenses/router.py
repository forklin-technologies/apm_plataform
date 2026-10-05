"""Expenses, attachments, approval and reimbursement: /api/v1/schools/{school_id}/...

Every route carries exactly one permission marker (through `school_scope`) and checks that the
school in the path is the school of the session (a different one is the same 404 as a missing one).
Routes that write commit inside the service. Errors are problem+json with a stable `code`.
"""

from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.auth.permissions import Permission
from app.auth.responses import problem_responses
from app.core.errors import PROBLEM_MEDIA_TYPE, ProblemError
from app.expenses import service
from app.expenses.access import SchoolScope, school_scope
from app.expenses.attachment_types import ALLOWED_TYPES
from app.expenses.schemas import (
    ApproveRequest,
    AttachmentKind,
    AttachmentOut,
    CategoryList,
    ExpenseCreate,
    ExpenseDetail,
    ExpensePage,
    ExpenseStatus,
    ExpenseUpdate,
    ReasonRequest,
    ReimburseRequest,
)
from app.routers.deps import get_tenant_db
from app.schemas.problem import Problem
from app.storage import (
    AttachmentStore,
    StorageNotFoundError,
    StorageSettings,
    get_attachment_store,
    get_storage_settings,
)

router = APIRouter(prefix="/schools/{school_id}", tags=["expenses"])

TenantDb = Annotated[Session, Depends(get_tenant_db)]

_SUBMIT = (Permission.EXPENSES_SUBMIT,)
_READ = (Permission.EXPENSES_READ_OWN, Permission.EXPENSES_READ_ALL)
_FORM = (Permission.EXPENSES_SUBMIT, *_READ)
_APPROVE = (Permission.EXPENSES_APPROVE,)
_REIMBURSE = (Permission.REIMBURSEMENTS_REGISTER,)
_CANCEL = (Permission.EXPENSES_SUBMIT, Permission.EXPENSES_APPROVE)

CanSubmit = Annotated[SchoolScope, Depends(school_scope(*_SUBMIT))]
CanRead = Annotated[SchoolScope, Depends(school_scope(*_READ))]
CanUseForm = Annotated[SchoolScope, Depends(school_scope(*_FORM))]
CanApprove = Annotated[SchoolScope, Depends(school_scope(*_APPROVE))]
CanReimburse = Annotated[SchoolScope, Depends(school_scope(*_REIMBURSE))]
CanCancel = Annotated[SchoolScope, Depends(school_scope(*_CANCEL))]
Store = Annotated[AttachmentStore, Depends(get_attachment_store)]
Limits = Annotated[StorageSettings, Depends(get_storage_settings)]

# Room for the multipart envelope (boundaries, headers, the `kind` field) around the file.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
MAX_DECLARED_DIGITS = 15
ATTACHMENT_KINDS: tuple[AttachmentKind, ...] = ("INVOICE", "PAYMENT_PROOF", "OTHER")


def _permission(*permissions: Permission) -> dict[str, Any]:
    """`x-permission` is what the route walk checks; `x-permission-any` lists the alternatives of a
    route open to more than one permission."""
    extra: dict[str, Any] = {"x-permission": permissions[0].value}
    if len(permissions) > 1:
        extra["x-permission-any"] = [permission.value for permission in permissions]
    return extra


def _problem(description: str) -> dict[str, Any]:
    return {
        "model": Problem,
        "description": description,
        "content": {PROBLEM_MEDIA_TYPE: {"schema": {"$ref": "#/components/schemas/Problem"}}},
    }


NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: _problem("not_found: unknown, outside the session's school, or not yours to see")
}


def _errors(*codes: int, extra: dict[int | str, dict[str, Any]] | None = None) -> dict[Any, Any]:
    """The problems a route can answer. 422 is always there: every route has a path parameter to
    validate, and the framework would otherwise document its own 422 instead of the problem."""
    return problem_responses(*sorted({*codes, 422}), extra={**NOT_FOUND, **(extra or {})})


# --- categories -----------------------------------------------------------------------------------


@router.get(
    "/expense-categories",
    operation_id="expense_categories_list",
    summary="Categories an expense can be filed under (money out)",
    response_model=CategoryList,
    responses=_errors(401, 403, 409),
    openapi_extra=_permission(*_FORM),
)
def list_categories(scope: CanUseForm, db: TenantDb) -> CategoryList:
    return service.list_categories(db, scope)


# --- expenses -------------------------------------------------------------------------------------


@router.post(
    "/expenses",
    operation_id="expenses_create",
    summary="Create an expense as a DRAFT",
    status_code=201,
    response_model=ExpenseDetail,
    responses=_errors(401, 403, 409, 422),
    openapi_extra=_permission(*_SUBMIT),
)
def create_expense(body: ExpenseCreate, scope: CanSubmit, db: TenantDb) -> ExpenseDetail:
    return service.create_expense(db, scope, body)


@router.get(
    "/expenses",
    operation_id="expenses_list",
    summary="List expenses (all of them with expenses:read_all, otherwise your own)",
    response_model=ExpensePage,
    responses=_errors(401, 403, 409, 422),
    openapi_extra=_permission(*_READ),
)
def list_expenses(
    scope: CanRead,
    db: TenantDb,
    status: Annotated[ExpenseStatus | None, Query()] = None,
    period: Annotated[
        str | None, Query(max_length=7, description="A month, YYYY-MM, in the school's time zone")
    ] = None,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ExpensePage:
    return service.list_expenses(
        db, scope, status=status, period=period, cursor=cursor, limit=limit
    )


@router.get(
    "/expenses/{expense_id}",
    operation_id="expenses_get",
    summary="One expense, with its attachments and its reimbursement",
    response_model=ExpenseDetail,
    responses=_errors(401, 403, 409),
    openapi_extra=_permission(*_READ),
)
def get_expense(expense_id: UUID, scope: CanRead, db: TenantDb) -> ExpenseDetail:
    return service.get_expense(db, scope, expense_id)


@router.patch(
    "/expenses/{expense_id}",
    operation_id="expenses_update",
    summary="Edit an expense (its author, while DRAFT or CORRECTION_REQUESTED)",
    response_model=ExpenseDetail,
    responses=_errors(401, 403, 409, 422),
    openapi_extra=_permission(*_SUBMIT),
)
def update_expense(
    expense_id: UUID, body: ExpenseUpdate, scope: CanSubmit, db: TenantDb
) -> ExpenseDetail:
    return service.update_expense(db, scope, expense_id, body)


# --- attachments ----------------------------------------------------------------------------------

_UPLOAD_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {
                            "type": "string",
                            "format": "binary",
                            "description": "An image (PNG, JPEG, WebP) or a PDF, within the limit.",
                        },
                        "kind": {
                            "type": "string",
                            "enum": list(ATTACHMENT_KINDS),
                            "default": "OTHER",
                        },
                    },
                }
            }
        },
    }
}


@router.post(
    "/expenses/{expense_id}/attachments",
    operation_id="expense_attachments_upload",
    summary="Attach a file (multipart: `file`, and optionally `kind`)",
    status_code=201,
    response_model=AttachmentOut,
    responses=_errors(
        401,
        403,
        409,
        422,
        extra={
            411: _problem("length_required: the request must declare its size"),
            413: _problem("payload_too_large"),
            415: _problem("attachment_type_not_allowed"),
        },
    ),  # fmt: skip
    openapi_extra={**_permission(*_SUBMIT), **_UPLOAD_BODY},
)
async def upload_attachment(
    request: Request,
    expense_id: UUID,
    scope: CanSubmit,
    db: TenantDb,
    store: Store,
    limits: Limits,
) -> AttachmentOut:
    """The size is bounded BEFORE the body is read (the declared length), and again while the file
    is streamed to the store. The form is parsed here, after the permission and the school were
    checked, so a caller who may not upload costs nothing."""
    declared = request.headers.get("content-length", "")
    # ASCII digits only: str.isdigit() is also true for "²" or Arabic-Indic digits, which int()
    # refuses. A number of more than 15 digits is far over any limit (and int() of thousands of
    # digits is refused too).
    if not (declared.isascii() and declared.isdigit()):
        raise ProblemError(411, "length_required", "The request must declare its size")
    if len(declared) > MAX_DECLARED_DIGITS or int(declared) > (
        limits.attachment_max_bytes + MULTIPART_OVERHEAD_BYTES
    ):
        raise ProblemError(413, "payload_too_large", "The file is larger than the limit")
    form = await request.form(max_files=1, max_fields=4)
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise service.invalid_field("file", "required")
        kind = form.get("kind", "OTHER")
        if kind not in ATTACHMENT_KINDS:
            raise service.invalid_field("kind", "invalid")
        return await run_in_threadpool(
            service.add_attachment,
            db,
            scope,
            store,
            expense_id,
            kind=str(kind),
            file_name=upload.filename,
            stream=upload.file,
            max_bytes=limits.attachment_max_bytes,
        )
    finally:
        await form.close()


def _content_disposition(name: str) -> str:
    fallback = "".join(
        ch if ch.isascii() and ch.isprintable() and ch not in '"\\%' else "_" for ch in name
    )
    return (
        f"attachment; filename=\"{fallback or 'anexo'}\"; filename*=UTF-8''{quote(name, safe='')}"
    )


@router.get(
    "/expenses/{expense_id}/attachments/{attachment_id}",
    operation_id="expense_attachments_download",
    summary="Download an attachment (whoever can see the expense)",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "The file, as an attachment",
            "content": {kind.content_type: {} for kind in ALLOWED_TYPES},
        },
        **_errors(401, 403, 409),
    },
    openapi_extra=_permission(*_READ),
)
def download_attachment(
    expense_id: UUID, attachment_id: UUID, scope: CanRead, db: TenantDb, store: Store
) -> StreamingResponse:
    attachment = service.attachment_for_download(db, scope, expense_id, attachment_id)
    try:
        chunks = store.open(attachment["storage_key"])
    except StorageNotFoundError:
        raise ProblemError(500, "attachment_unavailable", "The file is not available") from None
    return StreamingResponse(
        chunks,
        media_type=attachment["content_type"],
        headers={
            "Content-Disposition": _content_disposition(attachment["file_name"]),
            "Content-Length": str(attachment["size_bytes"]),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, no-store",
        },
    )


# --- actions --------------------------------------------------------------------------------------

_STATE_ERRORS = _errors(401, 403, 409, 422)


@router.post(
    "/expenses/{expense_id}/submit",
    operation_id="expenses_submit",
    summary="Send the expense for approval (its author; needs an attachment)",
    response_model=ExpenseDetail,
    responses=_STATE_ERRORS,
    openapi_extra=_permission(*_SUBMIT),
)
def submit_expense(expense_id: UUID, scope: CanSubmit, db: TenantDb) -> ExpenseDetail:
    return service.submit(db, scope, expense_id)


@router.post(
    "/expenses/{expense_id}/approve",
    operation_id="expenses_approve",
    summary="Approve (never your own); `approved_amount_cents` may be lower for a collaborator",
    response_model=ExpenseDetail,
    responses=_STATE_ERRORS,
    openapi_extra=_permission(*_APPROVE),
)
def approve_expense(
    expense_id: UUID, scope: CanApprove, db: TenantDb, body: ApproveRequest | None = None
) -> ExpenseDetail:
    return service.approve(db, scope, expense_id, body or ApproveRequest())


@router.post(
    "/expenses/{expense_id}/reject",
    operation_id="expenses_reject",
    summary="Reject, with a reason (final; never your own)",
    response_model=ExpenseDetail,
    responses=_STATE_ERRORS,
    openapi_extra=_permission(*_APPROVE),
)
def reject_expense(
    expense_id: UUID, body: ReasonRequest, scope: CanApprove, db: TenantDb
) -> ExpenseDetail:
    return service.reject(db, scope, expense_id, body.reason)


@router.post(
    "/expenses/{expense_id}/request-correction",
    operation_id="expenses_request_correction",
    summary="Send it back to its author with what to correct (never your own)",
    response_model=ExpenseDetail,
    responses=_STATE_ERRORS,
    openapi_extra=_permission(*_APPROVE),
)
def request_correction(
    expense_id: UUID, body: ReasonRequest, scope: CanApprove, db: TenantDb
) -> ExpenseDetail:
    return service.request_correction(db, scope, expense_id, body.reason)


@router.post(
    "/expenses/{expense_id}/reimburse",
    operation_id="expenses_reimburse",
    summary="Register the reimbursement paid in the bank (a collaborator's approved expense)",
    response_model=ExpenseDetail,
    responses=_STATE_ERRORS,
    openapi_extra=_permission(*_REIMBURSE),
)
def reimburse_expense(
    expense_id: UUID, body: ReimburseRequest, scope: CanReimburse, db: TenantDb
) -> ExpenseDetail:
    return service.reimburse(db, scope, expense_id, body.payment_reference)


@router.post(
    "/expenses/{expense_id}/pay",
    operation_id="expenses_pay",
    summary="Register that the APM paid an approved expense of its own",
    response_model=ExpenseDetail,
    responses=_STATE_ERRORS,
    openapi_extra=_permission(*_REIMBURSE),
)
def pay_expense(expense_id: UUID, scope: CanReimburse, db: TenantDb) -> ExpenseDetail:
    return service.pay(db, scope, expense_id)


@router.post(
    "/expenses/{expense_id}/cancel",
    operation_id="expenses_cancel",
    summary="Cancel (the author: DRAFT or CORRECTION_REQUESTED; an approver: APPROVED)",
    response_model=ExpenseDetail,
    responses=_STATE_ERRORS,
    openapi_extra=_permission(*_CANCEL),
)
def cancel_expense(expense_id: UUID, scope: CanCancel, db: TenantDb) -> ExpenseDetail:
    return service.cancel(db, scope, expense_id)
