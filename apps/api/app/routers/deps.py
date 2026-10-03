"""The tenant context and the tenant-bound database session for a route, from the session.

`require_tenant_context` used to answer 401 always (there was no authentication). It returns the
context of the ACTIVE membership of the logged-in user, revalidated on every request; the session it
runs in was bound to that context by app.auth.deps.get_principal, so every transaction carries it.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.auth.deps import CurrentPrincipal, Db
from app.core.errors import ProblemError
from app.db.tenant import TenantContext


def require_tenant_context(principal: CurrentPrincipal) -> TenantContext:
    if principal.tenant is None:
        raise ProblemError(
            409, "context_required", "Choose which school or organization to act for first"
        )
    return principal.tenant


def get_tenant_db(
    _context: Annotated[TenantContext, Depends(require_tenant_context)], db: Db
) -> Session:
    """The request's session, already bound to the tenant context of the active membership."""
    return db
