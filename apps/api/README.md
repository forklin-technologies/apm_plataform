# APM Digital API

FastAPI + SQLAlchemy 2.x (sync, psycopg 3) + Alembic + PostgreSQL 16. Python 3.12, dependencies managed with [uv](https://docs.astral.sh/uv/) (`pyproject.toml` + `uv.lock`).

Multi-tenancy (organizations, schools, users, memberships, row level security, database roles) is explained in [`docs/tenancy.md`](../../docs/tenancy.md). Read it before touching the database layer.

Every command below has two variants:

- **Docker** — needs only Docker. Run from the **repository root**. This is the reference path.
- **Host (uv)** — needs [uv](https://docs.astral.sh/uv/getting-started/installation/) (`brew install uv`). Run from `apps/api/`, with the database up and migrated (`docker compose up -d --wait db migrate`).

Tests, migrations and the seed need the **admin** database credential, so in Docker they run in the `tools` service: `docker compose run --rm tools <command>`. The `api` service never receives that credential.

The API answers under the `/api` prefix (web and API share one origin behind a proxy, so there is no CORS).

## 1. Configure

From the repository root:

```bash
cp .env.example .env
```

`.env` is git-ignored. The values in `.env.example` are development placeholders, not secrets. If you set your own passwords (`POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, and the same passwords inside `DATABASE_URL` / `DATABASE_ADMIN_URL`):

- **They must be URL-safe: no `@ : / ? #` and no whitespace.** Compose embeds them, unescaped, in the database URLs. An unescaped `@` makes the URL parse part of the password as the host, so the app and the tools **refuse to start** when they detect a URL they cannot read safely (the error message never prints the value) instead of leaking it in connection errors.
- Generate safe ones with `openssl rand -hex 24`. Use a different value for each password.
- On the host (uv) variant you may use special characters in the URLs if you percent-encode them (`@` becomes `%40`). Compose does not encode, so keep the compose passwords URL-safe.

| Variable | Used by | Notes |
| --- | --- | --- |
| `ENV` | API, tools | `development`, `test` or `production`. **Required, no default.** In `production` the Swagger/ReDoc UIs are off and the seed refuses to run. |
| `DATABASE_URL` | API, tools | **Required, no default.** Connects as the **`apm_app`** role (no superuser, no BYPASSRLS, not the table owner). Must start with `postgresql+psycopg://`. Compose builds it from `APP_DB_PASSWORD`; the line in `.env` is only for the host (uv) variant. |
| `DATABASE_ADMIN_URL` | tools only | Admin credential, for Alembic, the seed and the tests. **Never given to the API process.** Compose builds it from `POSTGRES_*`. |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | compose `db`, tools | The admin of the database. Required by compose. |
| `APP_DB_PASSWORD` | compose | Password of the `apm_app` role. The `migrate` service creates the role with it. **Required by compose.** |
| `DB_HOST_PORT` | compose | Postgres on the host. Default `5435`, bound to `127.0.0.1`. |
| `API_HOST_PORT` | compose | API on the host. Default `8001`, bound to `127.0.0.1`. |

> **Rebuilding an existing preview?** Its env-file needs the new `APP_DB_PASSWORD` (a URL-safe value, different from `POSTGRES_PASSWORD`) before `docker compose -p <preview> --env-file <file> up -d --build --wait`. The `migrate` service then creates the roles and tables in the existing database.

If `ENV` or `DATABASE_URL` is missing, the API refuses to start.

## 2. Run

Docker (repository root):

```bash
docker compose up -d --build --wait   # db, then migrate (one-shot), then api; returns when healthy
docker compose ps -a                  # db and api "healthy", migrate "Exited (0)"
curl -i http://127.0.0.1:8001/api/health         # 200 {"status":"ok"}
curl -i http://127.0.0.1:8001/api/health/ready   # 200 {"status":"ready"}; 503 when the database is down
curl -s http://127.0.0.1:8001/api/openapi.json | head -c 200
```

`migrate` applies every migration and exits; `api` starts only after it completed successfully. Without `--wait`, `docker compose up -d --build` returns while `api` is still `starting`, so run `docker compose ps` until it says `healthy`.

Interactive docs (not in `production`): <http://127.0.0.1:8001/api/docs>.

Stop (keeps the database volume):

```bash
docker compose down
```

> Do **not** add `-v` unless you want to delete the database volume (`docker compose down -v` erases all data).

Host (uv), from `apps/api/` with the database up and migrated:

```bash
uv sync                                                    # create .venv with all dependencies
uv run uvicorn --factory app.main:create_app --reload --port 8001
```

On the host the settings are read from the real environment first, then from `apps/api/.env`, then from the repository root `.env` (the one you copied above).

## 3. Migrate

`docker compose up` runs the migrations through the one-shot `migrate` service. To run them yourself:

| | Docker (repository root) | Host (uv, in `apps/api/`) |
| --- | --- | --- |
| Apply all | `docker compose run --rm tools alembic upgrade head` | `uv run alembic upgrade head` |
| Current revision | `docker compose run --rm tools alembic current` | `uv run alembic current` |
| Revert all | `docker compose run --rm tools alembic downgrade base` | `uv run alembic downgrade base` |
| Preview the SQL (no database touched) | `docker compose run --rm tools alembic upgrade head --sql` | `uv run alembic upgrade head --sql` |
| New revision (written, then fixed and formatted by `ruff` through the `alembic.ini` post-write hooks) | `docker compose run --rm --user "$(id -u):$(id -g)" -v "$PWD/apps/api:/app" tools alembic revision -m "message"` | `uv run alembic revision -m "message"` |

> **A database that already applied the *earlier* revision `0005`** (the one before it also revoked `TEMPORARY` on the database) keeps the old privileges, because Alembic does not re-run an applied revision. Re-apply it once:
> `docker compose run --rm tools alembic downgrade 0004_tenancy_rls && docker compose run --rm tools alembic upgrade head` (host: `uv run alembic downgrade 0004_tenancy_rls && uv run alembic upgrade head`). `python -m app.posture` (below) reports `temporary_allowed` until you do.

Migrations run as the admin (`DATABASE_ADMIN_URL`) and do their DDL as the non-superuser `apm_owner` role. The password of `apm_app` is set by an online-only step as a SCRAM verifier: it never appears in `--sql`, in logs or in an error.

(The "new revision" Docker variant mounts the source so the generated file lands on your disk.)

## 4. Check the database posture

Is the API connecting as the unprivileged role, and does the database still have row level security enabled and forced, no unexpected `SECURITY DEFINER` function and no `TEMPORARY` privilege? The API runs this check when it starts (outside `ENV=test`) and refuses to start on any finding; this runs it on demand and prints only fixed codes.

| Docker (repository root) | Host (uv, in `apps/api/`) |
| --- | --- |
| `docker compose run --rm tools python -m app.posture` | `uv run python -m app.posture` |

Prints `posture OK`, or `posture FAILED: <codes>` and exits 1. Details: [`docs/tenancy.md`](../../docs/tenancy.md).

## 5. Seed (development only)

Fake data: two organizations, three schools, seven users (`@example.test`) and their memberships. Safe to run twice. It **refuses to run with `ENV=production`**, before opening any connection.

| Docker (repository root) | Host (uv, in `apps/api/`) |
| --- | --- |
| `docker compose run --rm tools python -m app.seed` | `uv run python -m app.seed` |

## 6. Test

One command. The isolation tests run against the configured database (so tampering with it makes them fail) and the migration tests create and drop their own scratch databases, which needs the admin credential: use `tools`. `tools` also starts `db-clean`, a second throwaway Postgres cluster (no published port, data in memory) that the tests use to run the migrations as a `CREATEROLE` admin that is not a superuser. Several tests briefly tamper with the shared roles and restore them: run **one test session at a time per cluster**.

| Docker (repository root) | Host (uv, in `apps/api/`) |
| --- | --- |
| `docker compose run --rm tools pytest` | `uv run pytest` |

## 7. Lint, format and typecheck

| Task | Docker (repository root) | Host (uv, in `apps/api/`) |
| --- | --- | --- |
| Lint | `docker compose run --rm tools ruff check .` | `uv run ruff check .` |
| Format check | `docker compose run --rm tools ruff format --check .` | `uv run ruff format --check .` |
| Format (apply) | `docker compose run --rm --user "$(id -u):$(id -g)" -v "$PWD/apps/api:/app" tools ruff format .` | `uv run ruff format .` |
| Typecheck (mypy strict) | `docker compose run --rm tools mypy` | `uv run mypy` |

## 8. Dependencies

Edit `pyproject.toml` and refresh the lock; commit both.

| Docker (repository root) | Host (uv, in `apps/api/`) |
| --- | --- |
| `docker run --rm --user "$(id -u):$(id -g)" -e UV_CACHE_DIR=/tmp/uv-cache -v "$PWD/apps/api:/work" -w /work ghcr.io/astral-sh/uv:0.9.30-python3.12-bookworm-slim uv lock` | `uv lock` |

The image build runs `uv sync --frozen`, so it fails if `uv.lock` is out of date.

## Layout

```
apps/api/
  app/
    main.py          create_app() factory (run with uvicorn --factory)
    core/config.py   Settings (API) and AdminSettings (tools); shared, leak-proof URL validation
    db/              Base (naming convention), engine/session factory, get_db, tenant context helper
    routers/         HTTP layer; everything mounted under /api (v1/: auth and invitations; deps.py: tenant context of the session)
    auth/            sessions, passwords, CSRF, rate limit, permissions, the three definer functions' callers (docs/auth.md)
    models/          Organization, School, User, Membership, UserSession, LoginAttempt, Invitation
    schemas/         Pydantic request/response models
    services/        business rules (empty for now)
    repositories/    data access (empty for now)
    seed.py          development seed (fake data, refuses production)
    posture.py       `python -m app.posture`; db/posture.py holds the checks
  migrations/        Alembic (roles, tables, row level security); env.py uses AdminSettings
  tests/
  Dockerfile         multi-stage, non-root user; compose builds the `dev` target
```

`docker build apps/api` (no target) produces the lean `runtime` image without test tooling; the container runs as the non-root user `app` (uid 10001).
