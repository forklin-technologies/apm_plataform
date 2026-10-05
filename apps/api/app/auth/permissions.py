"""Permissions: a map role -> permissions in CODE (ADR-016), and the markers every route must carry.

Every route declares exactly one of:
  require(Permission.X)  an authenticated user whose role holds that permission
  authenticated()        any authenticated user (the route is about the user themself)
  public()               no session needed: it must be listed in PUBLIC_ROUTES, with the reason
A test walks every registered route and fails when one has none, or has two, or is public without
being in the list.
"""

from enum import StrEnum


class Permission(StrEnum):
    # Access management (this task)
    INVITATIONS_CREATE = "invitations:create"
    MEMBERSHIPS_MANAGE = "memberships:manage"
    # Organization and school data
    ORGANIZATION_UPDATE = "organization:update"
    SCHOOL_UPDATE = "school:update"
    # Money (ADR-015/016): the routes arrive with the financial tasks, the permissions are
    # decided now
    CONTRIBUTIONS_RECORD_CASH = "contributions:record_cash"
    EXPENSES_SUBMIT = "expenses:submit"
    EXPENSES_READ_OWN = "expenses:read_own"
    EXPENSES_READ_ALL = "expenses:read_all"
    EXPENSES_APPROVE = "expenses:approve"
    REIMBURSEMENTS_REGISTER = "reimbursements:register"
    REFUNDS_REGISTER = "refunds:register"
    MONTHS_CLOSE = "months:close"
    MONTHS_REOPEN = "months:reopen"
    STATEMENT_READ = "statement:read"
    REPORTS_READ = "reports:read"
    REPORTS_READ_AGGREGATE = "reports:read_aggregate"


ALL_PERMISSIONS = frozenset(Permission)

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    # Everything in the organization.
    "organization_admin": ALL_PERMISSIONS,
    # Everything in the school, except reopening a closed month (organization_admin, ADR-015).
    "school_admin": ALL_PERMISSIONS - {Permission.MONTHS_REOPEN},
    "treasurer": frozenset(
        {
            Permission.CONTRIBUTIONS_RECORD_CASH,
            Permission.EXPENSES_READ_ALL,
            Permission.EXPENSES_APPROVE,
            Permission.REIMBURSEMENTS_REGISTER,
            Permission.REFUNDS_REGISTER,
            Permission.MONTHS_CLOSE,
            Permission.STATEMENT_READ,
            Permission.REPORTS_READ,
        }
    ),
    # Teachers and staff: submit and see their OWN expenses and reimbursements.
    "staff": frozenset({Permission.EXPENSES_SUBMIT, Permission.EXPENSES_READ_OWN}),
    # Aggregate reading only, no personal data of guardians.
    "viewer": frozenset({Permission.REPORTS_READ_AGGREGATE}),
}

# Roles an inviter may grant, by the inviter's own role.
INVITABLE_ROLES: dict[str, frozenset[str]] = {
    "organization_admin": frozenset(
        {"organization_admin", "school_admin", "treasurer", "staff", "viewer"}
    ),
    "school_admin": frozenset({"school_admin", "treasurer", "staff", "viewer"}),
}


def permissions_for(role: str | None) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role or "", frozenset())


class PermissionRequirement:
    """Marker + dependency: the route needs this permission. Built by `require()`."""

    def __init__(self, permission: Permission) -> None:
        self.permission = permission


class AuthenticatedMarker:
    """Marker: the route needs a session but no particular permission."""


class PublicMarker:
    """Marker: the route needs no session."""


# (method, path) -> why it is public. The walk of the routes compares this with reality.
PUBLIC_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/health"): "liveness probe",
    ("GET", "/api/health/ready"): "readiness probe",
    ("POST", "/api/v1/auth/login"): "proves who the person is; rate limited and Origin checked",
    ("POST", "/api/v1/auth/logout"): "idempotent; verifies CSRF when a session is presented",
    ("POST", "/api/v1/invitations/accept"): "the invitation token is the credential; rate limited",
    # The public contribution flow (ADR-010, ADR-018): the slug and the receipt token are the scope.
    ("GET", "/api/v1/public/schools/{slug}"): "the public page of a school",
    ("POST", "/api/v1/public/schools/{slug}/contributions"): "a family starts a contribution; "
    "revalidated, rate limited per address, Origin checked, Idempotency-Key",
    ("GET", "/api/v1/public/schools/{slug}/contributions/{token}/charge"): "the receipt token "
    "is the credential; unknown tokens count against the address",
    ("POST", "/api/v1/public/schools/{slug}/contributions/{token}/charges"): "the receipt "
    "token is the credential; a new charge only when the previous one expired",
    ("GET", "/api/v1/public/schools/{slug}/contributions/{token}/receipt"): "the receipt token "
    "is the credential; 200 only when PAID",
    ("POST", "/api/v1/webhooks/pix/sandbox"): "authenticated by X-Webhook-Secret (a hash is "
    "compared); no browser Origin; idempotent by event id; the provider is asked again",
    ("POST", "/api/v1/webhooks/pix/bb"): "authenticated by X-Webhook-Secret (a hash is compared); "
    "no browser Origin; the real provider arrives in M2",
    ("POST", "/api/v1/dev/sandbox/pix/{txid}/pay"): "development and tests only: 404 elsewhere",
}
# Documentation routes FastAPI registers itself (not APIRoute objects with dependencies).
PUBLIC_FRAMEWORK_PATHS = frozenset({"/api/openapi.json", "/api/docs", "/api/redoc"})
