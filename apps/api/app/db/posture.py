"""Database posture: is the connection what the isolation design assumes it is? (review N6)

Row level security only protects if the application connects as the unprivileged role, the tables
still have it enabled and forced, nothing outside a closed list runs with the owner's rights, and
nobody can plant temporary tables. A deployment that points the API at a superuser, or a database
someone tampered with, would otherwise run "fine" while every policy is silently bypassed.

The full check runs when the API starts (outside ENV=test) and as `python -m app.posture`; the
readiness probe repeats only the cheap part (who am I). Every message is made of fixed codes: no
value read from the database or from the settings is ever echoed.
"""

from sqlalchemy import Connection, text

from app.core.config import APP_DB_ROLE

TENANT_TABLES = ("organizations", "schools", "users", "memberships")
# The tables of the financial schema (0007, ADR-015). All of them belong to an organization and a
# school and carry row level security like the tenant tables.
FINANCIAL_TABLES = (
    "categories",
    "school_settings",
    "financial_transactions",
    "contributions",
    "expenses",
    "reimbursements",
    "refunds",
    "expense_attachments",
    "payment_accounts",
    "pix_charges",
    "webhook_events",
    "audit_logs",
    "monthly_closings",
)
# Every table with row level security: the tenant tables, the authentication tables and the
# financial ones.
RLS_TABLES = (*TENANT_TABLES, "sessions", "login_attempts", "invitations", *FINANCIAL_TABLES)
DEFINER_ROLE = "apm_definer"

# Who owns the tenant tables.
EXPECTED_TABLE_OWNER = "apm_owner"

# Every policy that may exist, as (table, policy, command, roles). Closed on purpose: a missing one
# opens a door, an extra one (a permissive policy someone added) opens another. Extend it in the
# same migration that adds a policy (docs/tenancy.md, "How to create a new tenant table").
EXPECTED_POLICIES: frozenset[tuple[str, str, str, str]] = frozenset(
    {
        ("organizations", "organizations_select", "SELECT", "public"),
        ("organizations", "organizations_insert", "INSERT", "public"),
        ("organizations", "organizations_update", "UPDATE", "public"),
        ("schools", "schools_select", "SELECT", "public"),
        ("schools", "schools_insert", "INSERT", "public"),
        ("schools", "schools_update", "UPDATE", "public"),
        ("memberships", "memberships_select", "SELECT", "public"),
        ("memberships", "memberships_insert", "INSERT", "public"),
        ("memberships", "memberships_update", "UPDATE", "public"),
        ("memberships", "memberships_delete", "DELETE", "public"),
        ("users", "users_select", "SELECT", "public"),
        # 0006: sessions are visible only to whoever holds the token (or is the logged-in user).
        ("sessions", "sessions_select", "SELECT", "public"),
        ("sessions", "sessions_insert", "INSERT", "public"),
        ("sessions", "sessions_update", "UPDATE", "public"),
        ("login_attempts", "login_attempts_select", "SELECT", "public"),
        ("login_attempts", "login_attempts_insert", "INSERT", "public"),
        ("login_attempts", "login_attempts_delete", "DELETE", "public"),
        ("invitations", "invitations_select", "SELECT", "public"),
        ("invitations", "invitations_insert", "INSERT", "public"),
        ("invitations", "invitations_update", "UPDATE", "public"),
        ("users", "users_select_self", "SELECT", "public"),
        ("users", "users_update_self", "UPDATE", "public"),
        # The ONLY permissive policies for apm_definer (ADR-016): one per table and command that
        # the three SECURITY DEFINER functions need, no more.
        ("users", "users_definer_select", "SELECT", "apm_definer"),
        ("users", "users_definer_insert", "INSERT", "apm_definer"),
        ("memberships", "memberships_definer_select", "SELECT", "apm_definer"),
        ("memberships", "memberships_definer_insert", "INSERT", "apm_definer"),
        ("organizations", "organizations_definer_select", "SELECT", "apm_definer"),
        ("schools", "schools_definer_select", "SELECT", "apm_definer"),
        ("invitations", "invitations_definer_select", "SELECT", "apm_definer"),
        ("invitations", "invitations_definer_update", "UPDATE", "apm_definer"),
        # 0008 (ADR-018): the three functions of the public flow.
        ("schools", "schools_definer_select_public", "SELECT", "apm_definer"),
        ("school_settings", "school_settings_definer_select", "SELECT", "apm_definer"),
        ("payment_accounts", "payment_accounts_definer_select", "SELECT", "apm_definer"),
        ("contributions", "contributions_definer_select", "SELECT", "apm_definer"),
        # 0007: a SELECT and an INSERT policy on each financial table, and an UPDATE one on every
        # table but the two that only grow (expense_attachments, audit_logs): 37. None of them is
        # open to apm_definer: the financial schema has no SECURITY DEFINER function.
        *(
            (table, f"{table}_{command.lower()}", command, "public")
            for table in FINANCIAL_TABLES
            for command in (
                ("SELECT", "INSERT")
                if table in ("expense_attachments", "audit_logs")
                else ("SELECT", "INSERT", "UPDATE")
            )
        ),
    }
)

# The SECURITY DEFINER functions that may exist, as `schema.name(argument types)`. The list is
# closed on purpose: the three of TASK-004 (ADR-016) and the three of the public flow (0008,
# ADR-018); a later task adds its own by an ADR.
ALLOWED_SECURITY_DEFINER: frozenset[str] = frozenset(
    {
        "public.find_login_identity(p_email text)",
        "public.list_memberships_for_user(p_user_id uuid)",
        "public.accept_invitation(p_token_hash bytea, p_full_name text, p_password_hash text, "
        "p_existing_user_id uuid)",
        "public.resolve_school_public(p_slug text)",
        "public.resolve_webhook_target(p_provider text, p_secret_hash text)",
        "public.resolve_receipt(p_token_hash text)",
    }
)

_ROLE_QUERY = text(
    "SELECT current_user, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication "
    "FROM pg_roles WHERE rolname = current_user"
)
_TABLES_QUERY = text(
    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname = ANY(:tables)"
)
_DEFINER_ROLE_QUERY = text(
    "SELECT rolsuper, rolbypassrls, rolreplication, rolcreatedb, rolcreaterole, rolcanlogin "
    "FROM pg_roles WHERE rolname = :role"
)
_DEFINERS_QUERY = text(
    "SELECT n.nspname || '.' || p.proname "
    "|| '(' || pg_get_function_identity_arguments(p.oid) || ')' "
    "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
    "WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
)
_MEMBERSHIPS_QUERY = text(
    "SELECT r.rolname FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid "
    "WHERE m.member = (SELECT oid FROM pg_roles WHERE rolname = current_user) ORDER BY 1"
)
_OWNERS_QUERY = text(
    "SELECT c.relname, pg_get_userbyid(c.relowner) FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname = ANY(:tables)"
)
_POLICIES_QUERY = text(
    "SELECT tablename, policyname, cmd, array_to_string(roles, ','), permissive "
    "FROM pg_policies WHERE schemaname = 'public'"
)
_TEMPORARY_QUERY = text(
    "SELECT has_database_privilege(current_user, current_database(), 'TEMPORARY')"
)


class PostureError(RuntimeError):
    """The database does not look like the isolation design assumes. The message is fixed text."""


def cheap_posture_ok(connection: Connection) -> bool:
    """The part cheap enough for the readiness probe: the role is the unprivileged one."""
    row = connection.execute(_ROLE_QUERY).one_or_none()
    return row is not None and tuple(row)[:3] == (APP_DB_ROLE, False, False)


def check_posture(connection: Connection) -> list[str]:
    """Every finding as a fixed code (empty list = fine)."""
    findings: list[str] = []
    row = connection.execute(_ROLE_QUERY).one_or_none()
    if row is None or row[0] != APP_DB_ROLE:
        findings.append("not_application_role")
    if row is not None:
        _, superuser, bypass_rls, create_role, create_db, replication = row
        for finding, present in (
            ("role_is_superuser", superuser),
            ("role_bypasses_rls", bypass_rls),
            ("role_can_create_roles", create_role),
            ("role_can_create_databases", create_db),
            ("role_can_replicate", replication),
        ):
            if present:
                findings.append(finding)

    tables = {
        name: (enabled, forced)
        for name, enabled, forced in connection.execute(_TABLES_QUERY, {"tables": list(RLS_TABLES)})
    }
    for table in RLS_TABLES:
        if table not in tables:
            findings.append(f"table_missing:{table}")
            continue
        enabled, forced = tables[table]
        if not enabled:
            findings.append(f"rls_not_enabled:{table}")
        if not forced:
            findings.append(f"rls_not_forced:{table}")

    # The role that owns the SECURITY DEFINER functions is unprivileged too: no login, no bypass.
    definer = connection.execute(_DEFINER_ROLE_QUERY, {"role": DEFINER_ROLE}).one_or_none()
    if definer is None:
        findings.append("definer_role_missing")
    elif any(tuple(definer)[:5]) or definer[5]:
        findings.append("definer_role_unexpected_attributes")

    # The application role must be a member of NO role: not the owner (it could SET ROLE to it) and
    # not a predefined one such as pg_read_all_data (it would read around the policies).
    for (member_of,) in connection.execute(_MEMBERSHIPS_QUERY):
        findings.append(f"role_membership:{member_of}")

    for table, owner in connection.execute(_OWNERS_QUERY, {"tables": list(RLS_TABLES)}):
        if owner != EXPECTED_TABLE_OWNER:
            findings.append(f"table_owner_unexpected:{table}")

    found_policies: set[tuple[str, str, str, str]] = set()
    for table, policy, command, roles, permissive in connection.execute(_POLICIES_QUERY):
        found_policies.add((table, policy, command, roles))
        if permissive != "PERMISSIVE":
            findings.append(f"policy_not_permissive:{table}.{policy}")
    for table, policy, _command, _roles in sorted(EXPECTED_POLICIES - found_policies):
        findings.append(f"policy_missing:{table}.{policy}")
    for table, policy, _command, _roles in sorted(found_policies - EXPECTED_POLICIES):
        findings.append(f"policy_unexpected:{table}.{policy}")

    for identity in sorted({r[0] for r in connection.execute(_DEFINERS_QUERY)}):
        if identity not in ALLOWED_SECURITY_DEFINER:
            findings.append(f"unexpected_security_definer:{identity}")

    if connection.execute(_TEMPORARY_QUERY).scalar_one():
        findings.append("temporary_allowed")
    return findings


def assert_posture(connection: Connection) -> None:
    findings = check_posture(connection)
    if findings:
        raise PostureError("database posture check failed: " + ", ".join(findings))
