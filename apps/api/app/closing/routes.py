"""POST/GET /schools/{school_id}/closings, GET one, POST .../verify and .../reopen."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.exc import DBAPIError

from app.auth.deps import Principal, require
from app.auth.permissions import Permission, permissions_for
from app.closing import queries
from app.closing.errors import closing_refusal, reopen_refusal
from app.closing.schemas import (
    CloseRequest,
    ClosingDetailOut,
    ClosingOut,
    ClosingPage,
    ReopenRequest,
    VerifyOut,
)
from app.core.errors import ProblemError
from app.statement.cursor import decode_cursor, encode_cursor
from app.statement.deps import (
    InSchool,
    TenantDb,
    not_found,
    permission_extra,
    require_any,
    responses_of,
)
from app.statement.periods import month_bounds, period_of

router = APIRouter(prefix="/api/v1/schools/{school_id}/closings", tags=["closings"])

READ = (Permission.STATEMENT_READ, Permission.REPORTS_READ_AGGREGATE)


def _visible(row: dict[str, Any], principal: Principal) -> dict[str, Any]:
    """The closing as the caller may see it. Whoever only reads aggregates (the `viewer`) gets the
    figures, not who closed or reopened the month, nor the free text of the reopening."""
    data = dict(row)
    data["period"] = period_of(data["period_start"])
    if Permission.STATEMENT_READ not in permissions_for(principal.role):
        data.update(closed_by_user_id=None, reopened_by_user_id=None, reopen_reason=None)
    return data


def closing_summary(row: dict[str, Any], principal: Principal) -> ClosingOut:
    return ClosingOut.model_validate(_visible(row, principal))


def closing_detail(row: dict[str, Any], principal: Principal) -> ClosingDetailOut:
    return ClosingDetailOut.model_validate(_visible(row, principal))


def _load(db: TenantDb, scope: InSchool, closing_id: UUID) -> dict[str, Any]:
    row = queries.get_closing(db, scope.school_id, closing_id)
    if row is None:
        raise not_found()  # not there, or the closing of another school: the same answer
    return row


@router.post(
    "",
    operation_id="closings_create",
    summary="Close a month: the database computes the snapshot",
    status_code=201,
    response_model=ClosingDetailOut,
    responses=responses_of(401, 403, 404, 409, 422),
    openapi_extra={"x-permission": Permission.MONTHS_CLOSE.value},
)
def create_closing(
    body: CloseRequest,
    principal: Annotated[Principal, Depends(require(Permission.MONTHS_CLOSE))],
    scope: InSchool,
    db: TenantDb,
) -> ClosingDetailOut:
    """Only the INSERT is done here. The month must have ended (in the time zone of the school),
    and months are closed in order; the figures, the hash and the breakdown are the database's."""
    start, _ = month_bounds(body.period)
    try:
        new_id = queries.insert_closing(
            db,
            organization_id=scope.organization_id,
            school_id=scope.school_id,
            period_start=start,
            user_id=principal.user_id,
            bank_balance_reported_cents=body.bank_balance_reported_cents,
        )
        db.commit()
    except DBAPIError as error:
        db.rollback()
        known = closing_refusal(error, next_period=_next_period(db, scope))
        if known is None:
            raise
        raise known from None
    return closing_detail(_load(db, scope, new_id), principal)


def _next_period(db: TenantDb, scope: InSchool) -> str | None:
    day = queries.next_period_to_close(db, scope.school_id)
    return None if day is None else period_of(day)


@router.get(
    "",
    operation_id="closings_list",
    summary="The closings of the school, newest first",
    response_model=ClosingPage,
    responses=responses_of(401, 403, 404, 409, 422),
    openapi_extra=permission_extra(*READ),
)
def list_closings(
    principal: Annotated[Principal, Depends(require_any(*READ))],
    scope: InSchool,
    db: TenantDb,
    include_reopened: Annotated[
        bool, Query(description="Also the closings that were reopened (superseded).")
    ] = True,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ClosingPage:
    after = (
        None if cursor is None else decode_cursor(cursor, instants=("closed_at",), uuids=("id",))
    )
    rows = queries.list_closings(
        db, scope.school_id, include_reopened=include_reopened, after=after, limit=limit + 1
    )
    page, more = rows[:limit], len(rows) > limit
    items = [closing_summary(row, principal) for row in page]
    next_cursor = (
        encode_cursor(closed_at=page[-1]["closed_at"], id=page[-1]["id"]) if more else None
    )
    return ClosingPage(items=items, next_cursor=next_cursor)


@router.get(
    "/{closing_id}",
    operation_id="closings_get",
    summary="One closing, with its breakdown, its verification code and the bank difference",
    response_model=ClosingDetailOut,
    responses=responses_of(401, 403, 404, 409, 422),
    openapi_extra=permission_extra(*READ),
)
def get_closing(
    closing_id: UUID,
    principal: Annotated[Principal, Depends(require_any(*READ))],
    scope: InSchool,
    db: TenantDb,
) -> ClosingDetailOut:
    return closing_detail(_load(db, scope, closing_id), principal)


@router.post(
    "/{closing_id}/verify",
    operation_id="closings_verify",
    summary="Recompute the closing from the ledger and compare it with the snapshot",
    response_model=VerifyOut,
    responses=responses_of(401, 403, 404, 409, 422),
    openapi_extra={"x-permission": Permission.STATEMENT_READ.value},
)
def verify_closing(
    closing_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.STATEMENT_READ))],
    scope: InSchool,
    db: TenantDb,
) -> VerifyOut:
    """Reads only (it changes nothing). Meaningful for an active closing: a reopened one was
    superseded and answers 409 `closing_reopened`."""
    row = _load(db, scope, closing_id)
    if row["reopened_at"] is not None:
        raise ProblemError(409, "closing_reopened", "The closing was reopened")
    return VerifyOut(
        closing_id=closing_id,
        verified=queries.verify_closing(db, closing_id),
        entries_hash=str(row["entries_hash"]),
    )


@router.post(
    "/{closing_id}/reopen",
    operation_id="closings_reopen",
    summary="Reopen the latest closing of the school, with a reason",
    response_model=ClosingDetailOut,
    responses=responses_of(401, 403, 404, 409, 422),
    openapi_extra={"x-permission": Permission.MONTHS_REOPEN.value},
)
def reopen_closing(
    closing_id: UUID,
    body: ReopenRequest,
    principal: Annotated[Principal, Depends(require(Permission.MONTHS_REOPEN))],
    scope: InSchool,
    db: TenantDb,
) -> ClosingDetailOut:
    """Only an organization administrator may (`months:reopen`), only the latest active closing,
    once. Closing the same month again creates a new row: the history stays."""
    _load(db, scope, closing_id)
    try:
        done = queries.reopen_closing(
            db, scope.school_id, closing_id, principal.user_id, body.reason
        )
        db.commit()
    except DBAPIError as error:
        db.rollback()
        known = reopen_refusal(error)
        if known is None:
            raise
        raise known from None
    if not done:
        raise ProblemError(409, "closing_already_reopened", "The closing was already reopened")
    return closing_detail(_load(db, scope, closing_id), principal)
