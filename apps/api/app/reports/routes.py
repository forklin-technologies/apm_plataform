"""GET /schools/{school_id}/closings/{closing_id}/report.pdf (the monthly closing, as a PDF)."""

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from app.auth.deps import Principal
from app.auth.permissions import Permission, permissions_for
from app.closing.queries import get_closing
from app.core.errors import ProblemError
from app.reports import queries
from app.reports.closing_report import ReportMismatch, build_closing_report, render_closing_report
from app.statement.deps import (
    InSchool,
    TenantDb,
    not_found,
    permission_extra,
    require_any,
    responses_of,
)

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
    the first time a PDF is made, and never again."""
    closing = get_closing(db, scope.school_id, closing_id)
    if closing is None:
        raise not_found()
    if closing["reopened_at"] is not None:
        raise ProblemError(409, "closing_reopened", "The closing was reopened")

    organization_name, school_name = queries.names(db, scope.school_id)
    generated_at = datetime.now(UTC)
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
            personal_data=Permission.REPORTS_READ in permissions_for(principal.role),
        )
    except ReportMismatch:
        raise ProblemError(
            409, "closing_mismatch", "The ledger no longer matches the snapshot of this closing"
        ) from None
    content = render_closing_report(report)

    if closing["report_ref"] is None:
        stamp = generated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        queries.set_report_ref(db, closing_id, f"pdf-v1:{stamp}:{closing['entries_hash'][:16]}")
        db.commit()
    period = closing["period_start"].strftime("%Y-%m")
    return pdf_response(content, f"fechamento-{period}.pdf")
