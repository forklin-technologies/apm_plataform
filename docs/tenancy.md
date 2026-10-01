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
| admin (`POSTGRES_USER`) | yes | the database admin; a **superuser in the dev compose**; in production a role with `CREATEROLE`, not a superuser | Alembic, the seed, the tests. **Never inside the API process** (`DATABASE_ADMIN_URL`, read only by `AdminSettings`) |
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

`apm_owner` and `apm_app` belong to the Postgres **cluster**, not to one database. So the role migration (`0002`) is idempotent and **re-asserts every attribute on each run** (a role someone loosened is repaired by upgrading), and the downgrade drops a role only when nothing else in the cluster depends on it (otherwise it keeps it and notes so). Tests create and drop scratch databases and never drop the shared roles.

### The password of `apm_app`

It comes from `DATABASE_URL` (the single source of truth) and is applied by `0002` as `ALTER ROLE apm_app PASSWORD '<SCRAM-SHA-256 verifier>'`: libpq computes the verifier client-side, so **the plain password is never sent to the server**, nor written to a statement log, `alembic upgrade head --sql` (offline mode skips this step with a comment), the Alembic output, an error message or a traceback (the statement goes straight to the driver and a failure re-raises a fixed message with the cause suppressed). Tests use canary passwords for all of this. Residual: the *verifier* (not the password) could appear in the server's statement log if `log_statement = 'all'` is enabled; it cannot be used to log in, but treat such logs as sensitive.

Both URLs go through the **same** validation (`validate_database_url`): the `@`-count rule, fixed error messages with no value echoed, `hide_input_in_errors`. `AdminSettings` also requires `DATABASE_URL` to connect as `apm_app` and `DATABASE_ADMIN_URL` not to.

## How to create a new tenant table

1. **Columns**: `organization_id uuid NOT NULL` (FK to `organizations`, `ON DELETE RESTRICT`) and, when the row belongs to a school, `school_id uuid`. Never put a tenant row in a table without `organization_id`.
2. **Composite foreign key** `(school_id, organization_id) REFERENCES schools (id, organization_id)` whenever the table has a `school_id`, so a row cannot mix tenants. Add indexes on the foreign keys.
3. **Create it as `apm_owner`**: in the migration, `SET LOCAL ROLE apm_owner` before the DDL and `RESET ROLE` after.
4. **`ENABLE` and `FORCE ROW LEVEL SECURITY`**, in the same migration, before any grant.
5. **Policies** per command, using `P(organization_id, school_id)`. `UPDATE` needs `USING` **and** `WITH CHECK`; `INSERT` needs `WITH CHECK`. Write no policy for a command nobody should run (default deny). Never `USING (true)`.
6. **Minimal grants** to `apm_app`, per command, per table, and per column when a column is sensitive. `UPDATE` **by column**, never on the columns that decide the tenant or who a row belongs to (`id`, `organization_id`, `school_id`, `user_id`). **A single-column foreign key to a global or tenant-owned table is checked without RLS**: do not give `apm_app` `INSERT` (or `UPDATE` of that column) on a table that points at another tenant's rows by one; route it through a function with its own ADR. No `ALTER DEFAULT PRIVILEGES`. Update the exact-grants test (`tests/test_app_role.py`).
7. **Model** in `app/models` (the test comparing the models with the migrated schema fails if they drift), with the same constraint and index names.
8. **A line in the isolation matrix** (`tests/test_isolation_matrix.py`): the new table in `TABLES`, its statements in `STATEMENTS`, and the expected set of contexts per operation in `EXPECTED_APP` and `EXPECTED_OWNER`. Add tenant-hop cases to `HOPS` if it has `organization_id` or `school_id`.
9. **Downgrade** that reverses every step (`DROP POLICY IF EXISTS`, `DROP TABLE IF EXISTS`), and update the policy count asserted in `tests/test_migrations.py`.

## Accepted risks (ADR-014)

- **The context setting can be forged by arbitrary SQL run as `apm_app`** (`set_config` is public). Row level security protects against **bugs in the application** (a forgotten `WHERE`, a wrong join), **not** against SQL injection or remote code execution. Mitigations: SQL is always parameterized, grants are minimal, `password_hash` is unreadable.
- **Reading without a context is denied, so some flows need their own ADR**: resolving a school by its public slug (ADR-010) and finding a user by e-mail at login (TASK-004) both happen before a tenant is known. The answer is a narrow `SECURITY DEFINER` function or a dedicated role, decided and reviewed on its own, not a bypass switch here.
- **Creating organizations, users and memberships is not possible as `apm_app`** (no `INSERT` grant; see "Why `apm_app` cannot create or re-point memberships"). Platform and invitation flows come with their own ADRs.
- **Known, accepted side channels** (checked in the review sweep, nothing of another tenant's data): a `UNIQUE` violation or `INSERT ... ON CONFLICT DO NOTHING` on `schools.slug` tells whether a slug is taken by *any* organization (slugs are global and public by design, ADR-010); `EXPLAIN` row estimates come from table-wide statistics (aggregate cardinality, no values); `pg_stats` is empty for the tenant tables for `apm_app` (RLS); a function created in `pg_temp` and used in a `WHERE` is only ever called on rows that already passed the policies.
- **`updated_at` is maintained by the ORM** (`onupdate`), not by a trigger, so raw SQL updates do not touch it.
- **The seed runs as the admin**, a superuser in development, so it bypasses row level security by design. It refuses `ENV=production` before connecting and creates fake data only.
- **Roles are cluster-wide**: two databases in one cluster share `apm_app`; the migration re-asserts its attributes and password on every upgrade.
