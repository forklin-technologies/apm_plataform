"""tenancy: database roles, schema privileges and the tenant-context functions

Revision ID: 0002_tenancy_roles
Revises: 0001_baseline
Create Date: 2026-10-01

Roles are cluster-wide, not per database, so every statement here is idempotent and the
downgrade only drops a role when nothing else in the cluster depends on it.
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
        END
        $$
        """
    )
    # Reassert the attributes on every run: a role that was tampered with is fixed by upgrading.
    op.execute(
        "ALTER ROLE apm_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
    )
    op.execute(
        "ALTER ROLE apm_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
        "NOBYPASSRLS NOINHERIT"
    )
    op.execute("ALTER ROLE apm_app SET search_path = public")
    _set_app_password()
    # The admin runs DDL as apm_owner (SET ROLE), so the tables belong to a role that is not a
    # superuser and FORCE ROW LEVEL SECURITY applies to their owner.
    op.execute("GRANT apm_owner TO CURRENT_USER")
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
                        EXECUTE format('DROP OWNED BY %I', role_name);
                        EXECUTE format('DROP ROLE %I', role_name);
                    EXCEPTION WHEN dependent_objects_still_exist THEN
                        RAISE NOTICE 'role % is used by another database, keeping it', role_name;
                    END;
                END IF;
            END LOOP;
        END
        $$
        """
    )
