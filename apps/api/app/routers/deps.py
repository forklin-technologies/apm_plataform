from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.tenant import TenantContext, tenant_session


def require_tenant_context() -> TenantContext:
    """Where the tenant of a request will come from once authentication exists (TASK-004).

    Until then there is no trustworthy way to know it, so this always answers 401. It deliberately
    ignores headers, query and body: the tenant is never taken from what the client sends.
    """
    raise HTTPException(status_code=401, detail="Authentication is required")


def get_tenant_db(
    request: Request, context: Annotated[TenantContext, Depends(require_tenant_context)]
) -> Iterator[Session]:
    """Request-scoped session whose transactions carry the tenant context."""
    with tenant_session(request.app.state.session_factory, context) as session:
        yield session
