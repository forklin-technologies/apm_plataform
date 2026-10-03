"""auth: sessions, login attempts, invitations and the three SECURITY DEFINER functions (ADR-016)

Revision ID: 0006_auth_sessions
Revises: 0005_tenancy_write_grants
Create Date: 2026-10-03

Reading without a tenant context is what authentication needs (find the person behind an e-mail,
list their memberships, accept an invitation), and ADR-016 allows it ONLY through a closed list of
narrow SECURITY DEFINER functions. Because FORCE ROW LEVEL SECURITY binds the table owner too, a
function owned by apm_owner would read nothing, so the functions belong to their own role,
apm_definer: NOLOGIN, not a superuser, no BYPASSRLS, owner of nothing but these three functions,
with privileges per COLUMN and explicit policies `TO apm_definer`, each as narrow as it can be.
Nothing here bypasses row level security: the only permissive policies for apm_definer are the ones
listed below, and a test enumerates them.

Sessions are the other read without context. They get no function: `sessions` has ENABLE + FORCE
row level security keyed on transaction-local settings (app.session_id = the SHA-256 of the cookie
token, app.user_id once the session is validated), so without a valid token no row is visible.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_auth_sessions"
down_revision: str | None = "0005_tenancy_write_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MEMBERSHIP_ROLES = "'organization_admin', 'school_admin', 'treasurer', 'staff', 'viewer'"
LIVE_INVITATION = "accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()"
OWN_SESSION = "(id = (SELECT public.app_session_id()) OR user_id = (SELECT public.app_user_id()))"
ADMIN_OPTION_HINT = (
    "the admin role cannot create or administer the role apm_definer: it needs CREATEROLE "
    "(or to be a superuser), and to be the role that created apm_definer if it already exists"
)


def _scope(org_column: str, school_column: str) -> str:
    return (
        f"({org_column} = (SELECT public.app_org()) AND "
        f"((SELECT public.app_school()) IS NULL OR {school_column} = (SELECT public.app_school())))"
    )


def _create_definer_role() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'apm_definer') THEN
                CREATE ROLE apm_definer NOLOGIN;
            END IF;
            IF current_setting('is_superuser') = 'on' THEN
                ALTER ROLE apm_definer NOSUPERUSER NOCREATEDB NOREPLICATION NOBYPASSRLS;
            END IF;
            ALTER ROLE apm_definer NOLOGIN NOCREATEROLE;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION '{ADMIN_OPTION_HINT}' USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        """
        DO $$
        DECLARE
            r record;
            problems text := '';
        BEGIN
            SELECT rolsuper, rolbypassrls, rolreplication, rolcreatedb, rolcreaterole, rolcanlogin
              INTO r FROM pg_catalog.pg_roles WHERE rolname = 'apm_definer';
            IF r.rolsuper THEN problems := problems || ' apm_definer has SUPERUSER;'; END IF;
            IF r.rolbypassrls THEN problems := problems || ' apm_definer has BYPASSRLS;'; END IF;
            IF r.rolreplication THEN problems := problems || ' apm_definer has REPLICATION;'; END IF;
            IF r.rolcreatedb THEN problems := problems || ' apm_definer has CREATEDB;'; END IF;
            IF r.rolcreaterole THEN problems := problems || ' apm_definer has CREATEROLE;'; END IF;
            IF r.rolcanlogin THEN problems := problems || ' apm_definer has LOGIN;'; END IF;
            IF problems <> '' THEN
                RAISE EXCEPTION 'the role apm_definer has unexpected attributes (%): a cluster '
                    'administrator must correct them with ALTER ROLE, then run the migration again',
                    btrim(problems) USING ERRCODE = 'insufficient_privilege';
            END IF;
        END
        $$
        """
    )
    # The admin runs the function DDL as apm_definer (SET ROLE), like it does for apm_owner.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT pg_has_role(current_user, 'apm_definer', 'SET') THEN
                GRANT apm_definer TO CURRENT_USER;
            END IF;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION '{ADMIN_OPTION_HINT}' USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )


def _owner_objects() -> None:
    op.execute("SET LOCAL ROLE apm_owner")
    # A session can only point at a membership of ITS OWN user: (membership_id, user_id) is a
    # composite foreign key, and this is its target.
    op.execute(
        "ALTER TABLE memberships ADD CONSTRAINT uq_memberships_id_user_id UNIQUE (id, user_id)"
    )

    for function, returns, body in (
        (
            "app_session_id",
            "bytea",
            "decode(nullif(current_setting('app.session_id', true), ''), 'hex')",
        ),
        (
            "app_user_id",
            "uuid",
            "nullif(current_setting('app.user_id', true), '')::uuid",
        ),
    ):
        op.execute(
            f"""
            CREATE FUNCTION public.{function}() RETURNS {returns}
            LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog
            AS $body$ SELECT {body} $body$
            """
        )
        op.execute(f"REVOKE ALL ON FUNCTION public.{function}() FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{function}() TO apm_app, apm_owner")

    # The existing policies apply to every role, apm_definer included, and evaluating them calls the
    # context functions: they only read a setting, so the definer may call them (it has no tenant
    # context, so they match nothing for it; only its own policies let rows through).
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.app_org(), public.app_school(), "
        "public.app_session_id(), public.app_user_id() TO apm_definer"
    )

    op.execute(
        """
        CREATE TABLE sessions (
            id bytea NOT NULL,
            user_id uuid NOT NULL,
            membership_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            last_seen_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            revoked_at timestamptz,
            revoked_reason text,
            ip inet,
            user_agent_hash bytea,
            CONSTRAINT pk_sessions PRIMARY KEY (id),
            CONSTRAINT ck_sessions_id_is_a_sha256 CHECK (octet_length(id) = 32),
            CONSTRAINT ck_sessions_expires_after_creation CHECK (expires_at > created_at),
            CONSTRAINT ck_sessions_revoked_reason_valid CHECK (
                revoked_reason IS NULL OR revoked_reason IN
                ('logout', 'rotated', 'password_changed', 'membership_inactive', 'user_inactive')),
            CONSTRAINT fk_sessions_user_id_users
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT,
            -- Cannot point at a membership of ANOTHER user. MATCH SIMPLE: NULL = no context yet.
            CONSTRAINT fk_sessions_membership_id_user_id_memberships
                FOREIGN KEY (membership_id, user_id) REFERENCES memberships (id, user_id)
                ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX ix_sessions_user_id ON sessions (user_id)")
    op.execute("CREATE INDEX ix_sessions_membership_id ON sessions (membership_id)")

    op.execute(
        """
        CREATE TABLE login_attempts (
            id bigint GENERATED ALWAYS AS IDENTITY,
            kind text NOT NULL,
            subject_hmac bytea NOT NULL,
            ip_hmac bytea NOT NULL,
            succeeded boolean NOT NULL,
            attempted_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_login_attempts PRIMARY KEY (id),
            CONSTRAINT ck_login_attempts_kind_valid CHECK (kind IN ('login', 'password', 'invitation')),
            CONSTRAINT ck_login_attempts_hmacs_are_sha256 CHECK (
                octet_length(subject_hmac) = 32 AND octet_length(ip_hmac) = 32)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_login_attempts_pair ON login_attempts (kind, subject_hmac, ip_hmac, attempted_at)"
    )
    op.execute("CREATE INDEX ix_login_attempts_ip ON login_attempts (kind, ip_hmac, attempted_at)")
    op.execute(
        "CREATE INDEX ix_login_attempts_subject ON login_attempts (kind, subject_hmac, attempted_at)"
    )

    op.execute(
        f"""
        CREATE TABLE invitations (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            school_id uuid,
            email text NOT NULL,
            role text NOT NULL,
            token_hash bytea NOT NULL,
            invited_by_user_id uuid NOT NULL,
            expires_at timestamptz NOT NULL,
            accepted_at timestamptz,
            accepted_user_id uuid,
            revoked_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_invitations PRIMARY KEY (id),
            CONSTRAINT uq_invitations_token_hash UNIQUE (token_hash),
            CONSTRAINT ck_invitations_token_hash_is_a_sha256 CHECK (octet_length(token_hash) = 32),
            CONSTRAINT ck_invitations_email_format CHECK (email ~ '^[^@\\s]+@[^@\\s]+$' AND email = lower(email)),
            CONSTRAINT ck_invitations_role_valid CHECK (role IN ({MEMBERSHIP_ROLES})),
            CONSTRAINT ck_invitations_organization_admin_is_org_wide CHECK (
                role <> 'organization_admin' OR school_id IS NULL),
            CONSTRAINT ck_invitations_expires_after_creation CHECK (expires_at > created_at),
            CONSTRAINT ck_invitations_accepted_has_a_user CHECK (
                (accepted_at IS NULL) = (accepted_user_id IS NULL)),
            CONSTRAINT fk_invitations_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE RESTRICT,
            CONSTRAINT fk_invitations_school_id_organization_id_schools
                FOREIGN KEY (school_id, organization_id) REFERENCES schools (id, organization_id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_invitations_invited_by_user_id_users
                FOREIGN KEY (invited_by_user_id) REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_invitations_accepted_user_id_users
                FOREIGN KEY (accepted_user_id) REFERENCES users (id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX ix_invitations_organization_id ON invitations (organization_id)")
    op.execute(
        "CREATE UNIQUE INDEX uq_invitations_live_email_scope ON invitations "
        "(organization_id, email, COALESCE(school_id, '00000000-0000-0000-0000-000000000000'::uuid)) "
        "WHERE accepted_at IS NULL AND revoked_at IS NULL"
    )

    for table in ("sessions", "login_attempts", "invitations"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # --- policies for the application role ----------------------------------------------------
    op.execute(f"CREATE POLICY sessions_select ON sessions FOR SELECT USING {OWN_SESSION}")
    op.execute(
        "CREATE POLICY sessions_insert ON sessions FOR INSERT "
        "WITH CHECK (user_id = (SELECT public.app_user_id()))"
    )
    op.execute(
        f"CREATE POLICY sessions_update ON sessions FOR UPDATE USING {OWN_SESSION} WITH CHECK {OWN_SESSION}"
    )
    # login_attempts is not tenant data (nobody is logged in yet): only recent rows are visible,
    # only a "now" row can be written and only an old one can be removed.
    op.execute(
        "CREATE POLICY login_attempts_select ON login_attempts FOR SELECT "
        "USING (attempted_at > now() - interval '24 hours')"
    )
    op.execute(
        "CREATE POLICY login_attempts_insert ON login_attempts FOR INSERT "
        "WITH CHECK (attempted_at BETWEEN now() - interval '1 minute' AND now() + interval '1 minute')"
    )
    op.execute(
        "CREATE POLICY login_attempts_delete ON login_attempts FOR DELETE "
        "USING (attempted_at < now() - interval '24 hours')"
    )
    scope = _scope("organization_id", "school_id")
    op.execute(f"CREATE POLICY invitations_select ON invitations FOR SELECT USING {scope}")
    op.execute(
        "CREATE POLICY invitations_insert ON invitations FOR INSERT WITH CHECK "
        f"({scope} AND invited_by_user_id = (SELECT public.app_user_id()) "
        "AND accepted_at IS NULL AND revoked_at IS NULL)"
    )
    op.execute(
        f"CREATE POLICY invitations_update ON invitations FOR UPDATE USING {scope} WITH CHECK {scope}"
    )
    # A user always sees, and may change the password of, THEIR OWN row. Nothing else of users.
    op.execute(
        "CREATE POLICY users_select_self ON users FOR SELECT USING (id = (SELECT public.app_user_id()))"
    )
    op.execute(
        "CREATE POLICY users_update_self ON users FOR UPDATE "
        "USING (id = (SELECT public.app_user_id())) WITH CHECK (id = (SELECT public.app_user_id()))"
    )

    # --- grants for the application role (explicit, per column for writes) --------------------
    op.execute("GRANT SELECT, INSERT ON sessions TO apm_app")
    op.execute("GRANT UPDATE (last_seen_at, revoked_at, revoked_reason) ON sessions TO apm_app")
    op.execute("GRANT SELECT, INSERT, DELETE ON login_attempts TO apm_app")
    # token_hash is not readable by the application: an invitation is found by its hash, never listed.
    op.execute(
        "GRANT SELECT (id, organization_id, school_id, email, role, invited_by_user_id, expires_at, "
        "accepted_at, accepted_user_id, revoked_at, created_at, updated_at) ON invitations TO apm_app"
    )
    op.execute("GRANT INSERT ON invitations TO apm_app")
    op.execute("GRANT UPDATE (revoked_at, updated_at) ON invitations TO apm_app")
    op.execute("GRANT UPDATE (password_hash, updated_at) ON users TO apm_app")

    # --- what apm_definer may touch: columns and policies, as narrow as the three functions need
    op.execute("GRANT SELECT (id, email, password_hash, is_active) ON users TO apm_definer")
    op.execute("GRANT INSERT (email, full_name, password_hash) ON users TO apm_definer")
    op.execute(
        "GRANT SELECT (id, user_id, organization_id, school_id, role, status) ON memberships TO apm_definer"
    )
    op.execute(
        "GRANT INSERT (user_id, organization_id, school_id, role, status) ON memberships TO apm_definer"
    )
    op.execute("GRANT SELECT (id, name, slug) ON organizations TO apm_definer")
    op.execute("GRANT SELECT (id, organization_id, name, slug) ON schools TO apm_definer")
    op.execute(
        "GRANT SELECT (id, organization_id, school_id, email, role, token_hash, expires_at, "
        "accepted_at, revoked_at) ON invitations TO apm_definer"
    )
    op.execute(
        "GRANT UPDATE (accepted_at, accepted_user_id, updated_at) ON invitations TO apm_definer"
    )

    op.execute("CREATE POLICY users_definer_select ON users FOR SELECT TO apm_definer USING (true)")
    op.execute(
        "CREATE POLICY users_definer_insert ON users FOR INSERT TO apm_definer "
        "WITH CHECK (is_active AND password_hash IS NOT NULL)"
    )
    op.execute(
        "CREATE POLICY memberships_definer_select ON memberships FOR SELECT TO apm_definer "
        "USING (status = 'active')"
    )
    op.execute(
        "CREATE POLICY memberships_definer_insert ON memberships FOR INSERT TO apm_definer "
        "WITH CHECK (status = 'active')"
    )
    op.execute(
        "CREATE POLICY organizations_definer_select ON organizations FOR SELECT TO apm_definer "
        "USING (EXISTS (SELECT 1 FROM public.memberships m WHERE m.organization_id = organizations.id))"
    )
    op.execute(
        "CREATE POLICY schools_definer_select ON schools FOR SELECT TO apm_definer "
        "USING (EXISTS (SELECT 1 FROM public.memberships m WHERE m.school_id = schools.id))"
    )
    # An UPDATE also checks the NEW row against the SELECT policy, and an accepted invitation is no
    # longer "live", so this one lets accepted rows through; accept_invitation() filters them out
    # itself, and only the UPDATE policy below is restricted to live invitations.
    op.execute(
        "CREATE POLICY invitations_definer_select ON invitations FOR SELECT TO apm_definer "
        "USING (revoked_at IS NULL AND expires_at > now())"
    )
    op.execute(
        f"CREATE POLICY invitations_definer_update ON invitations FOR UPDATE TO apm_definer "
        f"USING ({LIVE_INVITATION}) WITH CHECK (accepted_at IS NOT NULL AND accepted_user_id IS NOT NULL)"
    )
    op.execute("RESET ROLE")


FIND_LOGIN_IDENTITY = """
CREATE FUNCTION public.find_login_identity(p_email text)
RETURNS TABLE (user_id uuid, password_hash text, is_active boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $fn$
    SELECT u.id, u.password_hash, u.is_active
    FROM public.users u
    WHERE lower(u.email) = lower(p_email)
    LIMIT 1
$fn$
"""

LIST_MEMBERSHIPS_FOR_USER = """
CREATE FUNCTION public.list_memberships_for_user(p_user_id uuid)
RETURNS TABLE (
    membership_id uuid, organization_id uuid, organization_name text, organization_slug text,
    school_id uuid, school_name text, school_slug text, role text, status text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $fn$
    SELECT m.id, o.id, o.name, o.slug, s.id, s.name, s.slug, m.role, m.status
    FROM public.memberships m
    JOIN public.organizations o ON o.id = m.organization_id
    LEFT JOIN public.schools s ON s.id = m.school_id
    WHERE m.user_id = p_user_id AND m.status = 'active'
    ORDER BY o.name, s.name NULLS FIRST, m.id
$fn$
"""

ACCEPT_INVITATION = """
CREATE FUNCTION public.accept_invitation(
    p_token_hash bytea, p_full_name text, p_password_hash text, p_existing_user_id uuid)
RETURNS TABLE (
    outcome text, user_id uuid, membership_id uuid, organization_id uuid, school_id uuid, role text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $fn$
#variable_conflict use_column
DECLARE
    inv record;
    existing record;
    accepted_user uuid;
    accepted_membership uuid;
    result_outcome text;
BEGIN
    -- Row level security hides revoked and expired invitations and the WHERE below hides accepted
    -- ones, so every reason for failure looks the same from here: no row.
    SELECT i.id AS id, i.organization_id AS organization_id, i.school_id AS school_id,
           i.email AS email, i.role AS role
      INTO inv
      FROM public.invitations i
     WHERE i.token_hash = p_token_hash AND i.accepted_at IS NULL
       FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'invalid'::text, NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid, NULL::text;
        RETURN;
    END IF;

    SELECT u.id AS id, u.is_active AS is_active INTO existing
      FROM public.users u WHERE lower(u.email) = lower(inv.email);
    IF FOUND THEN
        IF NOT existing.is_active THEN
            RETURN QUERY SELECT 'invalid'::text, NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid, NULL::text;
            RETURN;
        END IF;
        -- The token alone never lets anyone act as an existing account: its owner must be logged in.
        IF p_existing_user_id IS DISTINCT FROM existing.id THEN
            RETURN QUERY SELECT 'login_required'::text, NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid, NULL::text;
            RETURN;
        END IF;
        accepted_user := existing.id;
        result_outcome := 'accepted_existing_user';
    ELSE
        IF p_existing_user_id IS NOT NULL OR p_password_hash IS NULL
           OR p_full_name IS NULL OR btrim(p_full_name) = '' THEN
            RETURN QUERY SELECT 'invalid'::text, NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid, NULL::text;
            RETURN;
        END IF;
        INSERT INTO public.users (email, full_name, password_hash)
        VALUES (lower(inv.email), btrim(p_full_name), p_password_hash)
        RETURNING id INTO accepted_user;
        result_outcome := 'accepted_new_user';
    END IF;

    INSERT INTO public.memberships (user_id, organization_id, school_id, role, status)
    VALUES (accepted_user, inv.organization_id, inv.school_id, inv.role, 'active')
    RETURNING id INTO accepted_membership;

    UPDATE public.invitations
       SET accepted_at = now(), accepted_user_id = accepted_user, updated_at = now()
     WHERE id = inv.id;

    RETURN QUERY SELECT result_outcome, accepted_user, accepted_membership,
                        inv.organization_id, inv.school_id, inv.role;
EXCEPTION WHEN unique_violation THEN
    -- Already a member of that scope (a race, or an old membership): nothing is created.
    RETURN QUERY SELECT 'invalid'::text, NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid, NULL::text;
END
$fn$
"""


def _definer_functions() -> None:
    op.execute("GRANT USAGE, CREATE ON SCHEMA public TO apm_definer")
    op.execute("SET LOCAL ROLE apm_definer")
    for ddl, signature in (
        (FIND_LOGIN_IDENTITY, "find_login_identity(text)"),
        (LIST_MEMBERSHIPS_FOR_USER, "list_memberships_for_user(uuid)"),
        (ACCEPT_INVITATION, "accept_invitation(bytea, text, text, uuid)"),
    ):
        op.execute(ddl)
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO apm_app")
    op.execute("RESET ROLE")
    # CREATE was only needed to create the functions: it does not stay.
    op.execute("REVOKE CREATE ON SCHEMA public FROM apm_definer")


def upgrade() -> None:
    _create_definer_role()
    op.execute("GRANT USAGE ON SCHEMA public TO apm_definer")
    _owner_objects()
    _definer_functions()


def downgrade() -> None:
    op.execute("GRANT USAGE, CREATE ON SCHEMA public TO apm_definer")
    op.execute("SET LOCAL ROLE apm_definer")
    op.execute("DROP FUNCTION IF EXISTS public.accept_invitation(bytea, text, text, uuid)")
    op.execute("DROP FUNCTION IF EXISTS public.list_memberships_for_user(uuid)")
    op.execute("DROP FUNCTION IF EXISTS public.find_login_identity(text)")
    op.execute("RESET ROLE")

    op.execute("SET LOCAL ROLE apm_owner")
    for policy, table in (
        ("users_definer_select", "users"),
        ("users_definer_insert", "users"),
        ("users_select_self", "users"),
        ("users_update_self", "users"),
        ("memberships_definer_select", "memberships"),
        ("memberships_definer_insert", "memberships"),
        ("organizations_definer_select", "organizations"),
        ("schools_definer_select", "schools"),
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    op.execute("REVOKE UPDATE (password_hash, updated_at) ON users FROM apm_app")
    for table in ("users", "memberships", "organizations", "schools"):
        op.execute(f"REVOKE ALL ON {table} FROM apm_definer")
    op.execute("DROP TABLE IF EXISTS invitations")
    op.execute("DROP TABLE IF EXISTS login_attempts")
    op.execute("DROP TABLE IF EXISTS sessions")
    op.execute("ALTER TABLE memberships DROP CONSTRAINT IF EXISTS uq_memberships_id_user_id")
    op.execute("REVOKE ALL ON FUNCTION public.app_org(), public.app_school() FROM apm_definer")
    op.execute("DROP FUNCTION IF EXISTS public.app_user_id()")
    op.execute("DROP FUNCTION IF EXISTS public.app_session_id()")
    op.execute("RESET ROLE")

    op.execute("REVOKE ALL ON SCHEMA public FROM apm_definer")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'apm_definer') THEN
                BEGIN
                    BEGIN
                        DROP OWNED BY apm_definer;
                    EXCEPTION WHEN insufficient_privilege THEN
                        NULL;
                    END;
                    DROP ROLE apm_definer;
                EXCEPTION
                    WHEN dependent_objects_still_exist THEN
                        RAISE NOTICE 'role apm_definer is used by another database, keeping it';
                    WHEN insufficient_privilege THEN
                        RAISE NOTICE 'the admin role cannot drop role apm_definer, keeping it';
                END;
            END IF;
        END
        $$
        """
    )
