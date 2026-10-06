# Continuous integration (TASK-010)

The minimum gate of the MVP (ADR-017), in `.github/workflows/ci.yml`. It runs on every push to `main`
and on every pull request, needs no secret and deploys nothing. Production images, the reverse proxy,
backups and monitoring are M2 and are not here.

| Job | What it checks | Same thing on a laptop |
|---|---|---|
| `api` | `alembic upgrade head`, `ruff check`, `ruff format --check`, `mypy`, the database posture and the whole `pytest` suite, on Postgres 16 plus the second throwaway cluster the migration tests need | see below |
| `web` | `npm run typecheck`, `lint`, `test` and `build` in `apps/web` | `cd apps/web && npm ci && npm run typecheck && npm run lint && npm test && npm run build` |
| `secrets` | gitleaks over the **whole history** (the repository is public: a credential in any old commit is a leak) | `docker run --rm -v "$PWD:/repo" ghcr.io/gitleaks/gitleaks:v8.21.2 detect --source=/repo --config=/repo/.gitleaks.toml --redact --no-banner` |
| `audit` | `pip-audit` on the locked Python dependencies (`uv.lock`) and `npm audit --omit=dev --audit-level=high` | `uv export --frozen --no-hashes --no-emit-project > r.txt && uvx pip-audit --requirement r.txt --no-deps --disable-pip --strict` in `apps/api`, and `npm audit` in `apps/web` |

The end-to-end tests of the site (`apps/web/e2e`, Playwright) are **not** in CI: they need the API with
the demo seed and the dev server, and they write to the demo database. Run them by hand
(`apps/web/README.md`).

## The `api` job on a laptop

There is no `uv` and no Postgres to install: everything runs in Docker, as in CI.

```
cp .env.example .env     # and put random values in POSTGRES_PASSWORD, APP_DB_PASSWORD, AUTH_SECRET
docker compose --profile tools build tools
docker compose up -d --wait db
docker compose --profile tools up -d --wait db-clean
T() { docker compose --profile tools run --rm --no-deps -T -v "$PWD/docs:/docs:ro" tools "$@"; }
T alembic upgrade head
T ruff check . && T ruff format --check . && T mypy . && T python -m app.posture && T pytest -q
```

`docker compose up --build` does **not** rebuild the `tools` image: after changing the dependencies,
run the `build tools` line again or the tests run against the old ones. Run it from a git worktree and
mount the sources of that worktree with `-v "$PWD/apps/api:/app"` too.

## The secret scan

`.gitleaks.toml` keeps the default rules and allows **only the exact fake values** the tests use (a
token that does not exist, a password typed in a unit test, the canary of the migration test). A path is
never allowed as a whole, so a real token pasted into a test file is still caught.

When a new test needs a fake value that trips a rule: first prefer a value that does not look like a
credential. If that is not possible, add the exact string to `regexes` in `.gitleaks.toml` with a
comment saying what it is. Never allow a value you are not sure is fake. A finding on a real credential
means rotating it: removing it from the history does not unleak it.

The demo password of the seed lives only in `.maestri/private/` (git-ignored) and in the environment of
the person running the seed: it must never be committed, not even in a test.

## The audit

`pip-audit` runs with `--strict`: a dependency it cannot audit fails the job. `npm audit` fails from
`high` up on the production dependencies. Dependabot (`.github/dependabot.yml`) opens weekly pull
requests for `uv`, `npm`, `docker` and `github-actions`; CI runs on each of them.

The actions are pinned by major version (`actions/checkout@v4`). Pinning them by commit SHA is
recommended for production (M2); Dependabot keeps both forms up to date.
