"""GET /schools/{school_id}/closings/{closing_id}/report.pdf (the monthly closing, as a PDF)."""

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.auth.deps import Principal
from app.auth.permissions import Permission, permissions_for
from app.closing.queries import get_closing
from app.core.errors import ProblemError
from app.reports import queries
from app.reports.closing_report import ReportMismatch, build_closing_report, render_closing_report
from app.reports.contributions_report import (
    build_contributions_report,
    render_contributions_report,
)
from app.statement.deps import (
    InSchool,
    TenantDb,
    month_of,
    not_found,
    permission_extra,
    require_any,
    responses_of,
)
from app.statement.periods import PERIOD_PATTERN
from app.statement.queries import fetch_summary

router = APIRouter(prefix="/api/v1/schools/{school_id}", tags=["reports"])

READ = (Permission.REPORTS_READ, Permission.REPORTS_READ_AGGREGATE)
PDF_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "The PDF; without `reports:read` the version without personal data.",
        "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
    }
}


def pdf_response(content: bytes, filename: str) -> Response:
    """A PDF that is never cached or sniffed: it holds the money and, for management, names."""
    return Response(
        content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/closings/{closing_id}/report.pdf",
    operation_id="closings_report_pdf",
    summary="The PDF of a monthly closing, drawn from its snapshot",
    response_class=Response,
    responses=responses_of(401, 403, 404, 409, 422, extra=PDF_RESPONSE),
    openapi_extra=permission_extra(*READ),
)
def closing_report_pdf(
    closing_id: UUID,
    principal: Annotated[Principal, Depends(require_any(*READ))],
    scope: InSchool,
    db: TenantDb,
) -> Response:
    """Drawn from the stored snapshot of an ACTIVE closing, never recomputed: the sections must add
    up to the figures of the closing or no PDF is made (409 `closing_mismatch`). Whoever lacks
    `reports:read` (the `viewer`) gets the version without personal data. `report_ref` is written
    the first time MANAGEMENT makes a PDF of an active closing, and never again: a read-only role
    draws the PDF and writes nothing."""
    closing = get_closing(db, scope.school_id, closing_id)
    if closing is None:
        raise not_found()
    if closing["reopened_at"] is not None:
        raise ProblemError(409, "closing_reopened", "The closing was reopened")

    organization_name, school_name = queries.names(db, scope.school_id)
    generated_at = datetime.now(UTC)
    management = Permission.REPORTS_READ in permissions_for(principal.role)
    try:
        report = build_closing_report(
            closing=closing,
            entries=queries.period_entries(
                db, scope.school_id, closing["period_start"], closing["period_end"]
            ),
            pending=queries.pending_entries(db, scope.school_id),
            category_names=queries.category_names(db, scope.school_id),
            organization_name=organization_name,
            school_name=school_name,
            generated_at=generated_at,
            personal_data=management,
        )
    except ReportMismatch:
        raise ProblemError(
            409, "closing_mismatch", "The ledger no longer matches the snapshot of this closing"
        ) from None
    content = render_closing_report(report)

    if management and closing["report_ref"] is None:
        stamp = generated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        queries.set_report_ref(db, closing_id, f"pdf-v1:{stamp}:{closing['entries_hash'][:16]}")
        db.commit()
    period = closing["period_start"].strftime("%Y-%m")
    return pdf_response(content, f"fechamento-{period}.pdf")


@router.get(
    "/reports/contributions.pdf",
    operation_id="reports_contributions_pdf",
    summary="The PDF of the contributions of a month",
    response_class=Response,
    responses=responses_of(401, 403, 404, 409, 422, extra=PDF_RESPONSE),
    openapi_extra=permission_extra(*READ),
)
def contributions_pdf(
    principal: Annotated[Principal, Depends(require_any(*READ))],
    scope: InSchool,
    db: TenantDb,
    period: Annotated[
        str | None,
        Query(
            pattern=PERIOD_PATTERN,
            description="YYYY-MM, in the time zone of the school (default: now).",
        ),
    ] = None,
) -> Response:
    """Who gave what in the month, by channel. What the sections add up to is checked against
    `statement_summary` of the same period (409 `report_mismatch` if the ledger moved while it was
    being read, twice in a row). Whoever lacks `reports:read` (the `viewer`) gets initials."""
    chosen, start, end = month_of(db, scope, period)
    organization_name, school_name = queries.names(db, scope.school_id)
    personal_data = Permission.REPORTS_READ in permissions_for(principal.role)
    generated_at = datetime.now(UTC)
    for attempt in (1, 2):
        # Three reads, each with its own snapshot: a settlement that lands between them makes the
        # sections disagree with the summary. Read again once; a second miss is a 409.
        summary = fetch_summary(db, scope.school_id, start, end)
        if summary is None:
            raise not_found()
        try:
            report = build_contributions_report(
                settled=queries.settled_contributions(db, scope.school_id, start, end),
                unsettled=queries.unsettled_contributions(db, scope.school_id, start, end),
                summary=summary,
                organization_name=organization_name,
                school_name=school_name,
                period_start=start,
                period_end=end,
                generated_at=generated_at,
                personal_data=personal_data,
            )
            break
        except ReportMismatch:
            if attempt == 2:
                raise ProblemError(
                    409, "report_mismatch", "The ledger changed while the report was being made"
                ) from None
    return pdf_response(render_contributions_report(report), f"contribuicoes-{chosen}.pdf")
