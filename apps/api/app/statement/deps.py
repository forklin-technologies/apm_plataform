"""What every route under `/schools/{school_id}` shares: the school of the path, checked against the
session, and a permission marker that accepts more than one permission."""

from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.deps import CurrentPrincipal, Principal
from app.auth.permissions import Permission, PermissionRequirement, permissions_for
from app.auth.responses import problem_responses
from app.core.errors import PROBLEM_MEDIA_TYPE, ProblemError
from app.routers.deps import get_tenant_db
from app.schemas.problem import Problem

TenantDb = Annotated[Session, Depends(get_tenant_db)]


class _RequireAny(PermissionRequirement):
    """Marker + dependency: the role must hold `permission` or one of the alternatives.

    The route walk of the tests reads `.permission` (the first one) and compares it with
    `x-permission`; the alternatives go in `x-permission-alternatives`."""

    def __init__(self, permission: Permission, *alternatives: Permission) -> None:
        super().__init__(permission)
        self.alternatives = alternatives

    def __call__(self, principal: CurrentPrincipal) -> Principal:
        if principal.active is None:
            raise ProblemError(
                409, "context_required", "Choose which school or organization to act for first"
            )
        held = permissions_for(principal.role)
        if not held & {self.permission, *self.alternatives}:
            raise ProblemError(403, "permission_denied", "You are not allowed to do this")
        return principal


def require_any(permission: Permission, *alternatives: Permission) -> _RequireAny:
    """Declare that a route needs `permission` or any of `alternatives` (still ONE marker)."""
    return _RequireAny(permission, *alternatives)


def permission_extra(permission: Permission, *alternatives: Permission) -> dict[str, object]:
    """The `openapi_extra` that goes with `require_any`."""
    extra: dict[str, object] = {"x-permission": permission.value}
    if alternatives:
        extra["x-permission-alternatives"] = [item.value for item in alternatives]
    return extra


@dataclass(frozen=True)
class SchoolScope:
    """A school the session may act on: its id, its organization and its time zone."""

    school_id: UUID
    organization_id: UUID
    timezone: str


def responses_of(
    *codes: int, extra: dict[int | str, dict[str, Any]] | None = None
) -> dict[int | str, dict[str, Any]]:
    """The OpenAPI `responses` of a route of this package: the shared problem answers, plus 404 (the
    school or the closing is not there, or is not the caller's)."""
    added: dict[int | str, dict[str, Any]] = dict(extra or {})
    if 404 in codes:
        added[404] = {
            "model": Problem,
            "description": "not_found: no such school or record, or not one of the caller's",
            "content": {PROBLEM_MEDIA_TYPE: {"schema": {"$ref": "#/components/schemas/Problem"}}},
        }
    return problem_responses(*(code for code in codes if code != 404), extra=added)


def not_found() -> ProblemError:
    # One answer for "no such school", "a school of another organization" and "a school outside the
    # active membership": the caller cannot tell them apart.
    return ProblemError(404, "not_found", "Not found")


def school_in_context(school_id: UUID, db: TenantDb) -> SchoolScope:
    """The school named by the path, IF the active membership covers it.

    The tenant never comes from the path: the session is already bound to the tenant of the active
    membership, so row level security only shows the schools that membership covers (its own school,
    or every school of its organization for an organization-wide membership). The path id is looked
    up under that policy, and anything it does not show is answered exactly like a missing school.
    """
    row = db.execute(
        text(
            "SELECT s.id, s.organization_id, st.timezone "
            "FROM schools s JOIN school_settings st ON st.school_id = s.id "
            "WHERE s.id = :school_id"
        ),
        {"school_id": school_id},
    ).one_or_none()
    if row is None:
        raise not_found()
    return SchoolScope(school_id=row[0], organization_id=row[1], timezone=row[2])


InSchool = Annotated[SchoolScope, Depends(school_in_context)]
