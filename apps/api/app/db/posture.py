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
    }
)

# The SECURITY DEFINER functions that may exist, as `schema.name(argument types)`. The list is
# closed on purpose and empty until TASK-004 adds the approved ones (ADR-016).
ALLOWED_SECURITY_DEFINER: frozenset[str] = frozenset()

_ROLE_QUERY = text(
    "SELECT current_user, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication "
    "FROM pg_roles WHERE rolname = current_user"
)
_TABLES_QUERY = text(
    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname = ANY(:tables)"
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
        for name, enabled, forced in connection.execute(
            _TABLES_QUERY, {"tables": list(TENANT_TABLES)}
        )
    }
    for table in TENANT_TABLES:
        if table not in tables:
            findings.append(f"table_missing:{table}")
            continue
        enabled, forced = tables[table]
        if not enabled:
            findings.append(f"rls_not_enabled:{table}")
        if not forced:
            findings.append(f"rls_not_forced:{table}")

    # The application role must be a member of NO role: not the owner (it could SET ROLE to it) and
    # not a predefined one such as pg_read_all_data (it would read around the policies).
    for (member_of,) in connection.execute(_MEMBERSHIPS_QUERY):
        findings.append(f"role_membership:{member_of}")

    for table, owner in connection.execute(_OWNERS_QUERY, {"tables": list(TENANT_TABLES)}):
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
