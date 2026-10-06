"""Who is calling, and for which school: the dependency every expenses route uses.

The school in the path is CHECKED against the context of the session, never trusted: a school that
is outside the context answers the same 404 as one that does not exist. The organization comes from
the active membership, and so does the user; nothing about the tenant comes from the client.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.deps import CurrentPrincipal, Principal, require
from app.auth.permissions import Permission, PermissionRequirement, permissions_for
from app.core.errors import ProblemError
from app.routers.deps import get_tenant_db


def not_found() -> ProblemError:
    """The one answer for everything the caller may not see: unknown, other school, other author."""
    return ProblemError(404, "not_found", "Not found")


@dataclass(frozen=True)
class SchoolScope:
    organization_id: UUID
    school_id: UUID
    user_id: UUID
    role: str
    permissions: frozenset[Permission]

    @property
    def can_read_all(self) -> bool:
        return Permission.EXPENSES_READ_ALL in self.permissions


class RequireAnyOf(PermissionRequirement):
    """Marker + dependency: the role must hold at least ONE of these permissions.

    The route walk reads `.permission` (the first one) for `x-permission`; the routes also list the
    whole set in `x-permission-any`. It is needed because `treasurer` reads every expense
    (`expenses:read_all`) without holding `expenses:read_own`.
    """

    def __init__(self, *permissions: Permission) -> None:
        super().__init__(permissions[0])
        self.permissions = frozenset(permissions)

    def __call__(self, principal: CurrentPrincipal) -> Principal:
        if principal.active is None:
            raise ProblemError(
                409, "context_required", "Choose which school or organization to act for first"
            )
        if not self.permissions & permissions_for(principal.role):
            raise ProblemError(403, "permission_denied", "You are not allowed to do this")
        return principal


def school_scope(*permissions: Permission) -> Callable[..., SchoolScope]:
    """A dependency: the principal holds (one of) `permissions` and `{school_id}` is in context."""
    requirement = require(permissions[0]) if len(permissions) == 1 else RequireAnyOf(*permissions)

    def dependency(
        school_id: UUID,
        principal: Annotated[Principal, Depends(requirement)],
        db: Annotated[Session, Depends(get_tenant_db)],
    ) -> SchoolScope:
        tenant = principal.tenant
        if tenant is None or principal.active is None:  # require() guarantees both
            raise ProblemError(
                409, "context_required", "Choose which school or organization to act for first"
            )
        if tenant.school_id is not None:
            if tenant.school_id != school_id:
                raise not_found()
        else:
            # An organization-wide membership covers every school of ITS organization: row level
            # security shows only those, so another organization's school is simply not there.
            found = db.execute(
                text("SELECT 1 FROM schools WHERE id = :id"), {"id": school_id}
            ).first()
            if found is None:
                raise not_found()
        return SchoolScope(
            organization_id=tenant.organization_id,
            school_id=school_id,
            user_id=principal.user_id,
            role=principal.active.role,
            permissions=permissions_for(principal.active.role),
        )

    return dependency
