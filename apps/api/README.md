# APM Digital API

FastAPI + SQLAlchemy 2.x (sync, psycopg 3) + Alembic + PostgreSQL 16. Python 3.12, dependencies managed with [uv](https://docs.astral.sh/uv/) (`pyproject.toml` + `uv.lock`).

Every command below has two variants:

- **Docker** — needs only Docker. Run from the **repository root**. This is the reference path.
- **Host (uv)** — needs [uv](https://docs.astral.sh/uv/getting-started/installation/) (`brew install uv`). Run from `apps/api/`, with the database up (`docker compose up -d db`).

The API answers under the `/api` prefix (web and API share one origin behind a proxy, so there is no CORS).

## 1. Configure

From the repository root:

```bash
cp .env.example .env
```

`.env` is git-ignored. The values in `.env.example` are development placeholders, not secrets. Change `POSTGRES_PASSWORD` (and the password inside `DATABASE_URL`) if you want your own; keep it URL-safe.

| Variable | Used by | Notes |
| --- | --- | --- |
| `ENV` | API | `development`, `test` or `production`. **Required, no default.** In `production` the Swagger/ReDoc UIs are off. |
| `DATABASE_URL` | API | **Required, no default.** Must start with `postgresql+psycopg://`. Compose builds it for the container from `POSTGRES_*`; the line in `.env` is only for the host (uv) variant. |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | compose `db` | Required by compose. |
| `DB_HOST_PORT` | compose | Postgres on the host. Default `5435`, bound to `127.0.0.1`. |
| `API_HOST_PORT` | compose | API on the host. Default `8001`, bound to `127.0.0.1`. |

If `ENV` or `DATABASE_URL` is missing, the API refuses to start.

## 2. Run

Docker (repository root):

```bash
docker compose up -d --build --wait   # returns once db and api are both healthy
docker compose ps                        # both should say "healthy"
curl -i http://127.0.0.1:8001/api/health         # 200 {"status":"ok"}
curl -i http://127.0.0.1:8001/api/health/ready   # 200 {"status":"ready"}; 503 when the database is down
curl -s http://127.0.0.1:8001/api/openapi.json | head -c 200
```

Without `--wait`, `docker compose up -d --build` only waits for `db`: it returns while `api` is still `starting`, so run `docker compose ps` until it says `healthy`.

Interactive docs (not in `production`): <http://127.0.0.1:8001/api/docs>.

Stop (keeps the database volume):

```bash
docker compose down
```

> Do **not** add `-v` unless you want to delete the database volume (`docker compose down -v` erases all data).

Host (uv), from `apps/api/` with the database up:

```bash
uv sync                                                    # create .venv with all dependencies
uv run uvicorn --factory app.main:create_app --reload --port 8001
```

On the host the settings are read from the real environment first, then from `apps/api/.env`, then from the repository root `.env` (the one you copied above).

## 3. Migrate

Migrations are **manual**; they do not run when the container starts. The first one is an empty baseline (`0001_baseline`) with no domain tables.

| | Docker (repository root) | Host (uv, in `apps/api/`) |
| --- | --- | --- |
| Apply all | `docker compose run --rm api alembic upgrade head` | `uv run alembic upgrade head` |
| Current revision | `docker compose run --rm api alembic current` | `uv run alembic current` |
| Revert all | `docker compose run --rm api alembic downgrade base` | `uv run alembic downgrade base` |
| New revision | `docker compose run --rm --user "$(id -u):$(id -g)" -v "$PWD/apps/api:/app" api alembic revision -m "message"` | `uv run alembic revision -m "message"` |

(The "new revision" Docker variant mounts the source so the generated file lands on your disk.)

## 4. Test

One command; the readiness test needs the real database, so `db` must be up (compose starts it for you).

| Docker (repository root) | Host (uv, in `apps/api/`) |
| --- | --- |
| `docker compose run --rm api pytest` | `uv run pytest` |

## 5. Lint, format and typecheck

| Task | Docker (repository root) | Host (uv, in `apps/api/`) |
| --- | --- | --- |
| Lint | `docker compose run --rm api ruff check .` | `uv run ruff check .` |
| Format check | `docker compose run --rm api ruff format --check .` | `uv run ruff format --check .` |
| Format (apply) | `docker compose run --rm --user "$(id -u):$(id -g)" -v "$PWD/apps/api:/app" api ruff format .` | `uv run ruff format .` |
| Typecheck (mypy strict) | `docker compose run --rm api mypy` | `uv run mypy` |

## 6. Dependencies

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
    core/config.py   Settings (pydantic-settings), fail-fast on missing ENV/DATABASE_URL
    db/              Base (naming convention), engine/session factory, get_db dependency
    routers/         HTTP layer; everything mounted under /api
    schemas/         Pydantic request/response models
    services/        business rules (empty for now)
    repositories/    data access (empty for now)
    models/          SQLAlchemy models (empty: no domain tables yet)
  migrations/        Alembic (env.py reads the URL from Settings)
  tests/
  Dockerfile         multi-stage, non-root user; compose builds the `dev` target
```

`docker build apps/api` (no target) produces the lean `runtime` image without test tooling; the container runs as the non-root user `app` (uid 10001).
