"""tenancy: database roles, schema privileges and the tenant-context functions

Revision ID: 0002_tenancy_roles
Revises: 0001_baseline
Create Date: 2026-10-01

Roles are cluster-wide, not per database, so every statement here is idempotent and the
downgrade only drops a role when nothing else in the cluster depends on it.

Who runs it. The admin (DATABASE_ADMIN_URL) is either a superuser (the development compose) or, in
a managed service, a role with CREATEROLE that owns the database but is NOT a superuser. PostgreSQL
16 lets such a role create and administer the roles it created, but NOT set the privileged
attributes SUPERUSER, BYPASSRLS, REPLICATION or CREATEDB, not even to their default. So:
  - the roles are created with the default attributes (which are the safe ones);
  - the privileged attributes are re-asserted ONLY when the admin is a superuser;
  - every attribute is then VERIFIED from pg_roles, and a mismatch fails the migration with a
    clear message (a cluster administrator has to fix the role) instead of going on silently.
Re-running this revision is the only thing that repairs or detects a tampered role: `alembic
upgrade head` at head runs nothing. The unprivileged check at runtime is app/db/posture.py.
"""

from collections.abc import Sequence
from typing import Any

from alembic import context, op
from psycopg import sql
from sqlalchemy.engine import make_url

from app.core.config import APP_DB_ROLE, get_admin_settings

revision: str = "0002_tenancy_roles"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNER_ROLE = "apm_owner"


def _set_app_password() -> None:
    """Give apm_app the password found in DATABASE_URL, without ever writing it as plain text.

    ALTER ROLE ... PASSWORD takes no bind parameters, so the statement carries a literal. It is
    the SCRAM-SHA-256 verifier computed here (libpq does the hashing), never the password, so the
    server's statement log, `alembic --sql` and any error message cannot reveal the password. The
    statement goes straight to the driver, which keeps SQLAlchemy from echoing it in a traceback.
    """
    if context.is_offline_mode():
        op.execute("-- the password of apm_app is only set when the migration runs online")
        return
    password = make_url(get_admin_settings().database_url.get_secret_value()).password
    if not password:
        raise RuntimeError("DATABASE_URL must carry the password of the apm_app role")
    try:
        driver_connection: Any = op.get_bind().connection.driver_connection
        verifier = driver_connection.pgconn.encrypt_password(
            password.encode(), APP_DB_ROLE.encode(), b"scram-sha-256"
        ).decode()
        driver_connection.execute(
            sql.SQL("ALTER ROLE {role} PASSWORD {verifier}").format(
                role=sql.Identifier(APP_DB_ROLE), verifier=sql.Literal(verifier)
            )
        )
    except Exception:
        # Drop the original exception: it could quote the statement.
        raise RuntimeError("could not set the password of the apm_app role") from None


# A non-superuser admin that did not create the roles has no ADMIN OPTION on them, and every ALTER
# ROLE / GRANT below fails for it: that error is turned into this message.
CANNOT_ADMINISTER = (
    "the admin role cannot administer the roles apm_app and apm_owner (it did not create them and "
    "lacks ADMIN OPTION on them): run the migration as a superuser, or as the role that created them"
)
FIX_HINT = "a cluster administrator must correct them with ALTER ROLE, then run the migration again"

# Shared by the checks: collects `problems` for the attributes that only a superuser can change.
_PRIVILEGED_PROBLEMS = """
    FOR r IN
        SELECT rolname, rolsuper, rolbypassrls, rolreplication, rolcreatedb
        FROM pg_catalog.pg_roles WHERE rolname IN ('apm_owner', 'apm_app')
    LOOP
        IF r.rolsuper THEN problems := problems || ' ' || r.rolname || ' has SUPERUSER;'; END IF;
        IF r.rolbypassrls THEN problems := problems || ' ' || r.rolname || ' has BYPASSRLS;'; END IF;
        IF r.rolreplication THEN problems := problems || ' ' || r.rolname || ' has REPLICATION;'; END IF;
        IF r.rolcreatedb THEN problems := problems || ' ' || r.rolname || ' has CREATEDB;'; END IF;
    END LOOP;
"""
_OTHER_PROBLEMS = """
    FOR r IN
        SELECT rolname, rolcreaterole, rolcanlogin, rolinherit
        FROM pg_catalog.pg_roles WHERE rolname IN ('apm_owner', 'apm_app')
    LOOP
        IF r.rolcreaterole THEN problems := problems || ' ' || r.rolname || ' has CREATEROLE;'; END IF;
        IF r.rolname = 'apm_owner' AND r.rolcanlogin THEN problems := problems || ' apm_owner has LOGIN;'; END IF;
        IF r.rolname = 'apm_app' AND NOT r.rolcanlogin THEN problems := problems || ' apm_app has NOLOGIN;'; END IF;
        IF r.rolname = 'apm_app' AND r.rolinherit THEN problems := problems || ' apm_app has INHERIT;'; END IF;
    END LOOP;
"""


def _verification(checks: str) -> str:
    return f"""
        DO $$
        DECLARE
            r record;
            problems text := '';
        BEGIN
            {checks}
            IF problems <> '' THEN
                RAISE EXCEPTION 'the roles apm_app/apm_owner have unexpected attributes (%): {FIX_HINT}',
                    btrim(problems) USING ERRCODE = 'insufficient_privilege';
            END IF;
        END
        $$
        """


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'apm_owner') THEN
                CREATE ROLE apm_owner NOLOGIN;
            END IF;
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'apm_app') THEN
                CREATE ROLE apm_app LOGIN;
            END IF;
            -- Only a superuser may set SUPERUSER, BYPASSRLS, REPLICATION or CREATEDB, even to
            -- "off". Anyone else gets the defaults of CREATE ROLE, which are the safe values, and
            -- the check below turns anything else into a clear failure.
            IF current_setting('is_superuser') = 'on' THEN
                ALTER ROLE apm_owner NOSUPERUSER NOCREATEDB NOREPLICATION NOBYPASSRLS;
                ALTER ROLE apm_app NOSUPERUSER NOCREATEDB NOREPLICATION NOBYPASSRLS;
            END IF;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'the admin role cannot create or administer the roles apm_app and '
                'apm_owner: it needs CREATEROLE (or to be a superuser)'
                USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(_verification(_PRIVILEGED_PROBLEMS))
    # The attributes a role with CREATEROLE may set (and so repair).
    op.execute(
        f"""
        DO $$
        BEGIN
            ALTER ROLE apm_owner NOLOGIN;
            ALTER ROLE apm_app LOGIN NOINHERIT NOCREATEROLE;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION '{CANNOT_ADMINISTER}' USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(_verification(_PRIVILEGED_PROBLEMS + _OTHER_PROBLEMS))
    op.execute("ALTER ROLE apm_app SET search_path = public")
    _set_app_password()
    # The admin runs DDL as apm_owner (SET ROLE), so the tables belong to a role that is not a
    # superuser and FORCE ROW LEVEL SECURITY applies to their owner.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT pg_has_role(current_user, 'apm_owner', 'SET') THEN
                GRANT apm_owner TO CURRENT_USER;
            END IF;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION '{CANNOT_ADMINISTER}' USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            EXECUTE format(
                'GRANT CONNECT ON DATABASE %I TO apm_app, apm_owner', current_database()
            );
        END
        $$
        """
    )
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA public TO apm_app")
    op.execute("GRANT USAGE, CREATE ON SCHEMA public TO apm_owner")

    # Tenant context, read from transaction-local settings. NULL when unset, so a policy that
    # compares against it matches nothing (fail closed). SECURITY INVOKER with a fixed
    # search_path: no SECURITY DEFINER anywhere in the schema.
    op.execute("SET LOCAL ROLE apm_owner")
    for function, setting in (("app_org", "app.organization_id"), ("app_school", "app.school_id")):
        op.execute(
            f"""
            CREATE FUNCTION public.{function}() RETURNS uuid
            LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog
            AS $body$ SELECT nullif(current_setting('{setting}', true), '')::uuid $body$
            """
        )
        op.execute(f"REVOKE ALL ON FUNCTION public.{function}() FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{function}() TO apm_app, apm_owner")
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("SET LOCAL ROLE apm_owner")
    op.execute("DROP FUNCTION IF EXISTS public.app_school()")
    op.execute("DROP FUNCTION IF EXISTS public.app_org()")
    op.execute("RESET ROLE")
    op.execute("REVOKE ALL ON SCHEMA public FROM apm_app, apm_owner")
    op.execute(
        """
        DO $$
        BEGIN
            EXECUTE format(
                'REVOKE CONNECT ON DATABASE %I FROM apm_app, apm_owner', current_database()
            );
        END
        $$
        """
    )
    # Roles are cluster-wide: if another database still depends on one, it stays (that is not an
    # error, the next upgrade finds it and reasserts its attributes).
    op.execute(
        """
        DO $$
        DECLARE
            role_name text;
        BEGIN
            FOREACH role_name IN ARRAY ARRAY['apm_app', 'apm_owner']
            LOOP
                IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = role_name) THEN
                    BEGIN
                        BEGIN
                            EXECUTE format('DROP OWNED BY %I', role_name);
                        EXCEPTION WHEN insufficient_privilege THEN
                            NULL;  -- an admin without the role's privileges owns nothing to drop here
                        END;
                        EXECUTE format('DROP ROLE %I', role_name);
                    EXCEPTION
                        WHEN dependent_objects_still_exist THEN
                            RAISE NOTICE 'role % is used by another database, keeping it', role_name;
                        WHEN insufficient_privilege THEN
                            RAISE NOTICE 'the admin role cannot drop role %, keeping it', role_name;
                    END;
                END IF;
            END LOOP;
        END
        $$
        """
    )
