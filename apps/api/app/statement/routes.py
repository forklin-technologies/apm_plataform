"""GET /schools/{school_id}/statement, /statement/summary and /statement/pending."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.auth.deps import Principal, require
from app.auth.permissions import Permission
from app.statement import queries
from app.statement.cursor import decode_cursor, encode_cursor
from app.statement.deps import (
    InSchool,
    TenantDb,
    not_found,
    permission_extra,
    require_any,
    responses_of,
)
from app.statement.periods import PERIOD_PATTERN, month_bounds
from app.statement.schemas import (
    PendingEntryOut,
    PendingPage,
    StatementEntryOut,
    StatementPage,
    SummaryOut,
)

router = APIRouter(prefix="/api/v1/schools/{school_id}/statement", tags=["statement"])

Period = Annotated[
    str | None,
    Query(
        pattern=PERIOD_PATTERN,
        description="A calendar month, YYYY-MM, in the time zone of the school (default: now).",
    ),
]
Limit = Annotated[int, Query(ge=1, le=100, description="Rows per page (1 to 100).")]
Cursor = Annotated[str | None, Query(max_length=200, description="`next_cursor` of the last page.")]
# The status vocabulary of the database; a value outside it simply matches nothing.
StatusFilter = Annotated[str | None, Query(pattern=r"^[A-Z_]{1,40}$")]


def month_of(db: TenantDb, scope: InSchool, period: str | None) -> tuple[str, date, date]:
    """The month asked for (or the current one in the time zone of the school) and its bounds."""
    chosen = period if period is not None else queries.current_period(db, scope.school_id)
    if chosen is None:
        raise not_found()
    start, end = month_bounds(chosen)
    return chosen, start, end


@router.get(
    "",
    operation_id="statement_list",
    summary="The cash statement of a month, with the balance after every entry",
    response_model=StatementPage,
    responses=responses_of(401, 403, 404, 409, 422),
    openapi_extra={"x-permission": Permission.STATEMENT_READ.value},
)
def list_statement(
    principal: Annotated[Principal, Depends(require(Permission.STATEMENT_READ))],
    scope: InSchool,
    db: TenantDb,
    period: Period = None,
    kind: Annotated[
        Literal["CONTRIBUTION", "EXPENSE", "REIMBURSEMENT", "REFUND"] | None, Query()
    ] = None,
    display_type: Annotated[
        Literal["INCOME", "EXPENSE", "REFUND"] | None,
        Query(description="The coarser type of the statement (a reimbursement is an EXPENSE)."),
    ] = None,
    status: StatusFilter = None,
    category: Annotated[UUID | None, Query(description="Id of a category.")] = None,
    person: Annotated[
        UUID | None, Query(description="User id of the origin or of the beneficiary.")
    ] = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> StatementPage:
    """The settled cash entries of the month, oldest first. The columns are those of the database
    function `statement_entries`; the opening and the running balances are computed over ALL the
    entries, so a filtered statement still shows the balance of the account."""
    _, start, end = month_of(db, scope, period)
    after = (
        None
        if cursor is None
        else decode_cursor(cursor, instants=("settled_at",), integers=("reference_code",))
    )
    rows = queries.fetch_entries(
        db,
        scope.school_id,
        start,
        end,
        display_type=display_type,
        kind=kind,
        category=category,
        status=status,
        person=person,
        after=after,
        limit=limit + 1,
    )
    page, more = rows[:limit], len(rows) > limit
    items = [StatementEntryOut.model_validate(row) for row in page]
    next_cursor = (
        encode_cursor(settled_at=items[-1].settled_at, reference_code=items[-1].reference_code)
        if more
        else None
    )
    return StatementPage(items=items, next_cursor=next_cursor)


@router.get(
    "/summary",
    operation_id="statement_summary",
    summary="The dashboard of a month: the figures of statement_summary, both balances",
    response_model=SummaryOut,
    responses=responses_of(401, 403, 404, 409, 422),
    openapi_extra=permission_extra(Permission.STATEMENT_READ, Permission.REPORTS_READ_AGGREGATE),
)
def statement_summary(
    principal: Annotated[
        Principal,
        Depends(require_any(Permission.STATEMENT_READ, Permission.REPORTS_READ_AGGREGATE)),
    ],
    scope: InSchool,
    db: TenantDb,
    period: Period = None,
) -> SummaryOut:
    """The totals are aggregates (no person in them), so the read-only `viewer` may see them. The
    PRIMARY balance is `closing_balance_cents` (in cash); `balance_after_pending_cents` is the
    SECONDARY one (minus the reimbursements still to be paid, as of now)."""
    chosen, start, end = month_of(db, scope, period)
    row = queries.fetch_summary(db, scope.school_id, start, end)
    if row is None:
        raise not_found()
    return SummaryOut(period=chosen, period_start=start, period_end=end, **row)


@router.get(
    "/pending",
    operation_id="statement_pending",
    summary="What is not settled yet, outside the balance",
    response_model=PendingPage,
    responses=responses_of(401, 403, 404, 409, 422),
    openapi_extra={"x-permission": Permission.STATEMENT_READ.value},
)
def statement_pending(
    principal: Annotated[Principal, Depends(require(Permission.STATEMENT_READ))],
    scope: InSchool,
    db: TenantDb,
    section: Annotated[
        Literal["AWAITING_APPROVAL", "AWAITING_CORRECTION", "REVIEW", "RECEIVABLE", "PAYABLE"]
        | None,
        Query(),
    ] = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> PendingPage:
    """The rows of the database function `statement_pending`, oldest first."""
    after = (
        None
        if cursor is None
        else decode_cursor(cursor, instants=("occurred_at",), integers=("reference_code",))
    )
    rows = queries.fetch_pending(db, scope.school_id, section=section, after=after, limit=limit + 1)
    page, more = rows[:limit], len(rows) > limit
    items = [PendingEntryOut.model_validate(row) for row in page]
    next_cursor = (
        encode_cursor(occurred_at=items[-1].occurred_at, reference_code=items[-1].reference_code)
        if more
        else None
    )
    return PendingPage(items=items, next_cursor=next_cursor)
