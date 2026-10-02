# Multi-tenancy in the database

Decision record: ADR-014 (`architectural-decisions`). Code: `apps/api/migrations/versions/0002..0005`, `apps/api/app/models`, `apps/api/app/db/tenant.py`. This page explains how it works, how to extend it, and what it deliberately does not protect against.

## The model

The **tenant is the organization**; a school is a sub-scope of it. Every table is snake_case English, `uuid` primary keys (`gen_random_uuid()`), `timestamptz` timestamps.

| Table | Purpose | Key constraints |
| --- | --- | --- |
| `organizations` | the tenant | `slug` unique (lowercase, digits, hyphens) |
| `schools` | sub-scope of an organization | `organization_id NOT NULL` → organizations; `slug` **globally** unique (the public portal resolves `/apm/{slug}`, ADR-010); `UNIQUE (id, organization_id)` is the target of the composite foreign keys |
| `users` | **global identity** (not tenant data) | e-mail unique **ignoring case** (`UNIQUE INDEX ON lower(email)`); `password_hash` nullable until TASK-004 |
| `memberships` | what a user may do in a tenant | `user_id`, `organization_id NOT NULL`, `school_id` NULL = the whole organization, `role`, `status` |

`memberships` is where isolation is enforced by shape: `FOREIGN KEY (school_id, organization_id) REFERENCES schools (id, organization_id)` means a row can never name an organization and a school that belong to different tenants. With `school_id` NULL the constraint is skipped (`MATCH SIMPLE`), which is how organization-wide access is stored; `organization_id` still has its own foreign key.

Roles (a text `CHECK`, not a native enum, so migrating is easy): `organization_admin` (only with `school_id` NULL), `school_admin`, `treasurer`, `staff`, `viewer`. Statuses: `invited` (default), `active`, `suspended`, `revoked`. The permission matrix (role → permissions) lives **in code**, in TASK-004, not in tables.

**Why `platform_admin` is not a membership role.** A platform administrator does not belong to an organization, and `memberships.organization_id` is `NOT NULL` on purpose. Putting a platform role there would either force a fake organization or weaken the invariant that every membership row has a tenant. Platform flows (jobs, super admin, creating tenants) need their own ADR and their own database role (ADR-014).

## The tenant context

The database reads the tenant from two **transaction-local** settings: `app.organization_id` and `app.school_id`. Two functions turn them into UUIDs, `NULL` when unset:

```sql
public.app_org()     -- nullif(current_setting('app.organization_id', true), '')::uuid
public.app_school()  -- nullif(current_setting('app.school_id', true), '')::uuid
```

Both are `STABLE`, `SECURITY INVOKER`, with a fixed `search_path = pg_catalog`, owned by `apm_owner`. **There is no `SECURITY DEFINER` function anywhere in the schema** (a test enforces it).

Context semantics: `(organization, None)` is an organization-wide context (every school of it); `(organization, school)` is "working inside that school" (only that school's rows).

### The helper (`app/db/tenant.py`)

```python
from app.db.tenant import TenantContext, tenant_session

ctx = TenantContext(organization_id=org_id, school_id=None)   # explicit, built from trusted data
with tenant_session(session_factory, ctx) as session:          # commit on success, rollback on error
    schools = session.scalars(select(School)).all()
```

- `TenantContext(organization_id: UUID, school_id: UUID | None = None)` is frozen and rejects non-UUIDs.
- `apply_tenant_context(connection, ctx)` runs `set_config(..., true)` with **bound parameters** for the current transaction.
- `bind_tenant(session, ctx)` stores the context in `session.info` and registers an `after_begin` listener that **re-applies it at the start of every transaction** of that session, so `session.commit()` followed by more work keeps the context. A session that was never bound starts each transaction with no context and, with RLS failing closed, sees and writes nothing.
- `tenant_session(factory, ctx)` is the normal way: open, bind, yield, commit or roll back.

The context is **never** taken from a request (header, query, body). The example FastAPI dependency `app.routers.deps.require_tenant_context` answers **401** unconditionally until authentication exists (TASK-004); `get_tenant_db` builds on it. Because `set_config(..., true)` is transaction-local, a pooled connection never carries a context into its next user (a test reuses a single-connection pool to prove it).

## Row level security

`ENABLE` + **`FORCE`** row level security on all four tables: `FORCE` makes the table owner subject to the policies too, so only a superuser or a `BYPASSRLS` role skips them. Policies are `PERMISSIVE`, for all roles. **Fail closed**: with no context `app_org()` is `NULL`, `col = NULL` is never true, and no row is visible or writable.

Scope predicate `P(org, school)`: `org = app_org() AND (app_school() IS NULL OR school = app_school())`.

| Table | SELECT | INSERT (`WITH CHECK`) | UPDATE (`USING` and `WITH CHECK`) | DELETE |
| --- | --- | --- | --- | --- |
| `organizations` | `id = app_org()` | `id = app_org()` (only the owner can: see grants) | `id = app_org()` | no policy |
| `schools` | `P(organization_id, id)` | `organization_id = app_org() AND app_school() IS NULL` | `P(organization_id, id)` | no policy |
| `memberships` | `P(organization_id, school_id)` | `P(organization_id, school_id)` (only the owner can: `apm_app` has no `INSERT`) | `P(organization_id, school_id)` | `P(organization_id, school_id)` |
| `users` | a **visible** membership of that user exists (`EXISTS` over `memberships` with `P`) | no policy | no policy | no policy |

A **tenant hop** (an `UPDATE` or `INSERT` that tries to move a row to another organization, school or user) is stopped in layers. For `apm_app` the column privileges stop it first (`permission denied`: it cannot update `organization_id`, `school_id`, `user_id` or `id`, nor insert memberships). Behind that, `WITH CHECK` on the policies and the composite foreign key still stop the owner, who has every privilege (the tests run every hop as both roles and assert which layer refused it). An organization-wide membership row (`school_id` NULL) only shows up in an organization-wide context.

No policy is `USING (true)` and nothing reads a "system" flag: the legacy `app.sistema = 'on'` switch, which any injected SQL could flip, does not exist here.

## Database roles, grants and connections

| Identity | Login | Is | Used by |
| --- | --- | --- | --- |
| admin (`POSTGRES_USER`) | yes | the database admin: a **superuser in the dev compose**; in a managed service a role with `CREATEROLE` that **owns the database and is not a superuser** (supported and tested, see below) | Alembic, the seed, the tests. **Never inside the API process** (`DATABASE_ADMIN_URL`, read only by `AdminSettings`) |
| `apm_owner` | no (`NOLOGIN`) | owns the tables and functions; `NOSUPERUSER NOBYPASSRLS`. The admin runs DDL as it (`SET LOCAL ROLE apm_owner`) | migrations |
| `apm_app` | yes | what the API connects as (`DATABASE_URL`): `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT`, owns nothing, no `CREATE` on schema `public` | the API |

Why a non-superuser owner: a superuser ignores `FORCE ROW LEVEL SECURITY`, so with a superuser owner nobody could prove that removing `FORCE` breaks anything. The tests simulate the owner with `SET ROLE apm_owner`.

Grants are **explicit per table and, for writes, per column**; there are no default privileges, so a table created later starts with no access for `apm_app`:

| Table | `apm_app` privileges | Why |
| --- | --- | --- |
| `organizations` | `SELECT`; `UPDATE (name, updated_at)` | no `INSERT`/`DELETE` (tenants are created by a platform flow); `slug` is not mutable by the application: renaming changes URLs and uniqueness for everyone |
| `schools` | `SELECT`, `INSERT`; `UPDATE (name, updated_at)` | no `DELETE` (archiving comes later); `slug` is not mutable: `/apm/{slug}` is the public address (ADR-010) printed in links and QR codes |
| `memberships` | `SELECT`, `DELETE`; `UPDATE (role, status, updated_at)` | **no `INSERT`** and no update of `user_id`, `organization_id`, `school_id`: see "Why `apm_app` cannot create or re-point memberships" |
| `users` | `SELECT (id, email, full_name, is_active, created_at, updated_at)` | **`password_hash` is not readable** until TASK-004; the ORM model declares it `deferred` so `session.get(User, id)` works. No write privilege |

Rule: **never grant `UPDATE` on `id`, `organization_id`, `school_id`, `user_id` or `created_at`**, and never table-level `UPDATE` on a tenant table. Which role may change `role` or `status` is decided by the permission matrix in code (TASK-004); the database only guarantees that such a change cannot move the row to another tenant or user. Tests assert the exact column privileges of `apm_app` from the catalog, so a stray grant fails the suite.

### Why `apm_app` cannot create or re-point memberships

`memberships.user_id` references `users(id)`, and **foreign key checks run without row level security**, so the database accepts the id of *any* existing user, including one that belongs to another tenant. `users_select` shows a user to anyone with a *visible* membership of that user. Together, with `INSERT` (or `UPDATE` of `user_id`) on `memberships` an admin of organization A who knew the UUID of a user that only belongs to organization B could add that user to A, then read the person's e-mail, name and `is_active`, and also tell which UUIDs exist from the foreign key error. This is exactly what the first review found (M1) and a test now reproduces and denies: with the grants removed, the `INSERT` and the `UPDATE` fail with `permission denied` *before* any foreign key is evaluated, so an existing and a made-up UUID get the identical error, and the user keeps 0 rows in `users`.

Creating a membership is therefore **not an application-role operation**. It needs the invitation flow of **TASK-004**, implemented as a narrow `SECURITY DEFINER` function that checks who may invite whom and that the user is the invitee, decided in its own ADR (`SECURITY DEFINER` is deliberately absent from this task and a test enforces that). Until then the seed and the owner create memberships; the policy `memberships_insert` exists for the owner (and is what the isolation matrix uses to prove `FORCE`).

`apm_app` cannot `ALTER TABLE`, `DISABLE ROW LEVEL SECURITY`, `DROP POLICY`, `CREATE` objects, `SET ROLE` to the owner or the admin, or read `pg_authid`; `SET row_security = off` makes a query error instead of bypassing the policies (all tested).

### Roles are cluster-wide, migrations are per database

`apm_owner` and `apm_app` belong to the Postgres **cluster**, not to one database, so the role revision (`0002`) is written to be re-run: it creates what is missing, never fails because a role exists, and its downgrade drops a role only when nothing else in the cluster depends on it (otherwise it keeps it and says so).

**Which admin can run it.**

- A **superuser** (the dev compose): the revision re-asserts every attribute of both roles and verifies them.
- A **`CREATEROLE` admin that owns the database and is not a superuser** (a managed service; PostgreSQL 16): it creates the roles with the default attributes, which are the safe ones, and then *verifies* every attribute from `pg_roles`. Such a role **cannot** set `SUPERUSER`, `BYPASSRLS`, `REPLICATION` or `CREATEDB` (not even to "off"), so the revision only issues those `ALTER ROLE` statements when the admin is a superuser, and fails with a clear message if a loosened attribute is found (a cluster administrator must correct the role, then repeat). It can only administer roles **it created** (it holds `ADMIN OPTION` on them): if the roles were created by somebody else the migration stops with `cannot administer the roles apm_app and apm_owner`; the remedy is to run it as a superuser or `GRANT apm_app, apm_owner TO <admin> WITH ADMIN OPTION`. It must also own the database (it grants `CONNECT` and schema privileges) and be able to `SET ROLE apm_owner` (the revision grants that itself). All of this runs in the tests, in a **second throwaway cluster** (`db-clean`, no published port, data in memory) where the roles do not exist yet, so they are created by that very admin as in a managed service.

**What detects or repairs a loosened role.** `alembic upgrade head` at head runs nothing, so it neither detects nor repairs anything. Re-running revision `0002` does (a downgrade to `0001` and an upgrade): a superuser admin fixes the role; a `CREATEROLE` admin either drops and recreates it clean (when nothing else depends on it) or stops with the message above. Day to day, the detector is the **posture check** (next section).

### Posture check

Row level security only protects if the API connects as the unprivileged role and the database still has what the design assumes. `app/db/posture.py` checks, as `apm_app`: the current user *is* `apm_app` and is not a superuser, does not bypass RLS and cannot create roles, databases or replication; the four tables have RLS **enabled and forced**; no `SECURITY DEFINER` function exists outside a closed list (empty until TASK-004); and `TEMPORARY` is not granted. Every finding is a fixed code, never a value read from the database.

- **At startup**, outside `ENV=test`, the API refuses to start on any finding, and also when the database cannot be reached (it fails closed: serving without knowing is what the check prevents).
- **In readiness**, only the cheap part runs (who am I: `apm_app`, not superuser, no `BYPASSRLS`): the probe answers 503 if the role is loosened while the API runs.
- **As a command**: `docker compose run --rm tools python -m app.posture` prints `posture OK` or `posture FAILED: <codes>` (exit 1).

### The connection pool and the session

The tenant setting is transaction-local, so the pool's rollback ends it. State that outlives a transaction is a different matter: a `SET` of a session variable, a temporary table (which shadows a real table for whoever puts `pg_temp` first in the `search_path`), a cursor `WITH HOLD`, a prepared statement, an advisory lock, a `LISTEN`. The engine therefore runs **`DISCARD ALL` when a connection goes back to the pool** (a connection that cannot be cleaned is dropped), turns off automatic server-side prepared statements (they would be deallocated behind the driver's back), and `0005` **revokes `TEMPORARY` on the database** from `PUBLIC` and `apm_app`, so the role cannot create temporary tables at all. Tests plant every kind of state on a one-connection pool and prove the next checkout is clean (and a control without the hook proves the state does leak).

A `Session` is bound to **one** tenant context for its whole life (`bind_tenant` raises `TenantContextConflict` for another context, for a first bind inside a `SAVEPOINT`, and the identity map therefore never holds objects of two tenants); use a new session for another tenant.

### The password of `apm_app`

It comes from `DATABASE_URL` (the single source of truth) and is applied by `0002` as `ALTER ROLE apm_app PASSWORD '<SCRAM-SHA-256 verifier>'`: libpq computes the verifier client-side, so **the plain password is never sent to the server**, nor written to a statement log, `alembic upgrade head --sql` (offline mode skips this step with a comment), the Alembic output, an error message or a traceback (the statement goes straight to the driver and a failure re-raises a fixed message with the cause suppressed). Tests use canary passwords for all of this. Residual: the *verifier* (not the password) could appear in the server's statement log if `log_statement = 'all'` is enabled; it cannot be used to log in, but treat such logs as sensitive.

Both URLs go through the **same** validation (`validate_database_url`): the `@`-count rule, fixed error messages with no value echoed, `hide_input_in_errors`. `AdminSettings` also requires `DATABASE_URL` to connect as `apm_app` and `DATABASE_ADMIN_URL` not to, and **both URLs accept only an allow-list of query parameters**: `sslmode`, `sslrootcert`, `connect_timeout` and `application_name`, each at most once, matched exactly (case-sensitive). The driver turns every query parameter into a libpq connection option, so anything else (`user`, `password`, `host`, `hostaddr`, `dbname`, `service`, `passfile`, `options=-c row_security=off`, ...) could override what the URL says, or what the role checks verified, at connect time. It is an allow-list because a deny-list always misses a key; the error message is fixed and never echoes the key or its value.

## How to create a new tenant table

1. **Columns**: `organization_id uuid NOT NULL` (FK to `organizations`, `ON DELETE RESTRICT`) and, when the row belongs to a school, `school_id uuid`. Never put a tenant row in a table without `organization_id`.
2. **Composite foreign key** `(school_id, organization_id) REFERENCES schools (id, organization_id)` whenever the table has a `school_id`, so a row cannot mix tenants. Add indexes on the foreign keys.
3. **Create it as `apm_owner`**: in the migration, `SET LOCAL ROLE apm_owner` before the DDL and `RESET ROLE` after.
4. **`ENABLE` and `FORCE ROW LEVEL SECURITY`**, in the same migration, before any grant.
5. **Policies** per command, using `P(organization_id, school_id)`. `UPDATE` needs `USING` **and** `WITH CHECK`; `INSERT` needs `WITH CHECK`. Write no policy for a command nobody should run (default deny). Never `USING (true)`.
6. **Minimal grants** to `apm_app`, per command, per table, and per column when a column is sensitive. `UPDATE` **by column**, never on the columns that decide the tenant or who a row belongs to (`id`, `organization_id`, `school_id`, `user_id`). **A single-column foreign key to a global or tenant-owned table is checked without RLS**: do not give `apm_app` `INSERT` (or `UPDATE` of that column) on a table that points at another tenant's rows by one; route it through a function with its own ADR. No `ALTER DEFAULT PRIVILEGES`. Update the exact-grants test (`tests/test_app_role.py`).
7. **Model** in `app/models` (the test comparing the models with the migrated schema fails if they drift), with the same constraint and index names.
8. **A line in the isolation matrix** (`tests/test_isolation_matrix.py`): the new table in `TABLES`, its statements in `STATEMENTS`, and the expected set of contexts per operation in `EXPECTED_APP` and `EXPECTED_OWNER`. Add tenant-hop cases to `HOPS` if it has `organization_id` or `school_id`.
9. **Posture**: add the table to `TENANT_TABLES` in `app/db/posture.py`, so the startup check verifies that RLS stays enabled and forced on it.
10. **Downgrade** that reverses every step (`DROP POLICY IF EXISTS`, `DROP TABLE IF EXISTS`), and update the policy count asserted in `tests/test_migrations.py`.

## Accepted risks (ADR-014)

- **The context setting can be forged by arbitrary SQL run as `apm_app`** (`set_config` is public). Row level security protects against **bugs in the application** (a forgotten `WHERE`, a wrong join), **not** against SQL injection or remote code execution. Mitigations: SQL is always parameterized, grants are minimal, `password_hash` is unreadable.
- **Reading without a context is denied, so some flows need their own ADR**: resolving a school by its public slug (ADR-010) and finding a user by e-mail at login (TASK-004) both happen before a tenant is known. The answer is a narrow `SECURITY DEFINER` function or a dedicated role, decided and reviewed on its own, not a bypass switch here.
- **Creating organizations, users and memberships is not possible as `apm_app`** (no `INSERT` grant; see "Why `apm_app` cannot create or re-point memberships"). Platform and invitation flows come with their own ADRs.
- **`updated_at` is maintained by the ORM** (`onupdate`), not by a trigger, so raw SQL updates do not touch it.
- **The seed runs as the admin**, a superuser in development, so it bypasses row level security by design. It refuses `ENV=production` before connecting and creates fake data only.
- **Roles are cluster-wide**: two databases in one cluster share `apm_app` and its password; see "Roles are cluster-wide" for who can run the migration and what repairs a loosened role.
- **Checked and harmless** (tried in the review sweeps): `pg_stats` is empty for the tenant tables for `apm_app` (planner statistics are hidden under RLS); a function created in `pg_temp` and used in a `WHERE` (the classic RLS side channel) is only ever called on rows that already passed the policies; a `UNIQUE` violation shows only the value the caller supplied. A `UNIQUE` violation or `ON CONFLICT DO NOTHING` on `schools.slug` does tell whether a slug is taken by *any* organization (slugs are global and public by design, ADR-010).

### Risks that need arbitrary SQL as `apm_app` (the class ADR-014 accepts)

None of these is reachable through the application's own parameterized queries. They matter if SQL injection or code execution ever happens, and they are listed with their exact impact (each was tried against the real database):

- **`EXPLAIN ANALYZE` is an existence and count oracle.** Under RLS it still reports `Rows Removed by Filter`: for `SELECT id FROM users WHERE id = <uuid of a user of another tenant>` an index scan shows `1` against `0` for an unknown UUID, so any user UUID can be tested for existence; a plain `SELECT id FROM memberships` shows how many rows of *other* tenants were filtered out (exact counts). By e-mail it depends on the plan (on a large table the planner uses the `lower(email)` index and the signal appears; on the tiny test table it chose a sequential scan and did not). It returns **no column values**, only existence and counts. (The earlier "EXPLAIN only shows cardinality estimates" was wrong: `ANALYZE` executes the query.)
- **`INSERT ... ON CONFLICT (id) DO NOTHING` on `schools.id`** (or on `slug`) tells whether that id exists in *any* tenant (`rowcount` 0 against 1); a unique violation does the same. School slugs are public by design (ADR-010); school ids are not secret either, but the oracle exists.
- **The role can alter itself.** `ALTER ROLE apm_app PASSWORD '...'` (locks every instance of the application out until the next migration run), and `ALTER ROLE apm_app SET <parameter>` persists new defaults for every future session (for example `row_security = off`, which makes queries fail, or a different `search_path`). Availability and integrity of the setup, not tenant data.
- **`pg_terminate_backend` and `pg_cancel_backend`** work on any other session of the same role, so one session can kill requests of other tenants.
- **Large objects, `LISTEN`/`NOTIFY` and advisory locks are global to the database.** A large object created in a session of one tenant is readable from a session of another (same role); a `NOTIFY` reaches any `LISTEN`er; a held advisory lock blocks everyone who asks for the same key. The application uses none of them.
- **No `CONNECTION LIMIT` on `apm_app`** (`rolconnlimit = -1`), and no `statement_timeout` or `idle_in_transaction_session_timeout`: a runaway session can starve the others. Limits are set with the infrastructure (TASK-010).
- **A school-scoped context can, at the database level, rename its organization and delete memberships of its school** (the `organizations` update policy checks only the organization, and the memberships delete policy only the scope). The database cannot tell *who* is acting: the permission matrix by role in code (TASK-004) must forbid a school-level role from doing either.
- **The `TEMPORARY` privilege is revoked, but a superuser-created database default or a future `GRANT` could bring it back**: the posture check reports `temporary_allowed`.
