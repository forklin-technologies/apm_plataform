"""Development seed. FAKE data only; refuses to run with ENV=production.

    docker compose run --rm tools python -m app.seed

The demo users get a password (Argon2id): the value of SEED_PASSWORD, or a random one that is
printed ONCE when the users are created (never a value fixed in the repository). It is never logged
and an existing user keeps whatever password it has.

It connects with DATABASE_ADMIN_URL, which in development is a superuser, so it bypasses row level
security by design: the application role cannot create organizations or users (ADR-014). It is safe
to run twice: rows that already exist are left alone.
"""

import os
import secrets
import sys
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Connection, create_engine, text

from app.auth.passwords import MIN_LENGTH, hash_password
from app.seed_financial import seed_financial

PRODUCTION_REFUSAL = "refusing to seed: ENV=production. The seed is for development only."
FAKE_EMAIL_DOMAIN = "example.test"

# (slug, name)
ORGANIZATIONS = [
    ("demo-rede", "Rede Escolar Demo"),
    ("demo-instituto", "Instituto Exemplo"),
]
# (slug, name, organization slug)
SCHOOLS = [
    ("demo-aurora", "Escola Aurora (demo)", "demo-rede"),
    ("demo-horizonte", "Escola Horizonte (demo)", "demo-rede"),
    ("demo-central", "Escola Central (demo)", "demo-instituto"),
]
# (email, full name)
USERS = [
    ("ana.admin@example.test", "Ana Administradora (demo)"),
    ("bruno.diretor@example.test", "Bruno Diretor (demo)"),
    ("carla.tesoureira@example.test", "Carla Tesoureira (demo)"),
    ("diego.professor@example.test", "Diego Professor (demo)"),
    ("elisa.leitora@example.test", "Elisa Leitora (demo)"),
    ("fabio.admin@example.test", "Fabio Administrador (demo)"),
    ("gina.tesoureira@example.test", "Gina Tesoureira (demo)"),
    # Three memberships (two schools of one network and a school of another), to try the switch of
    # context in the site.
    ("helena.multipla@example.test", "Helena Multipla (demo)"),
]
# (email, organization slug, school slug or None for the whole organization, role)
MEMBERSHIPS = [
    ("ana.admin@example.test", "demo-rede", None, "organization_admin"),
    ("bruno.diretor@example.test", "demo-rede", "demo-aurora", "school_admin"),
    ("carla.tesoureira@example.test", "demo-rede", "demo-aurora", "treasurer"),
    ("diego.professor@example.test", "demo-rede", "demo-horizonte", "staff"),
    ("elisa.leitora@example.test", "demo-rede", "demo-horizonte", "viewer"),
    ("fabio.admin@example.test", "demo-instituto", None, "organization_admin"),
    ("gina.tesoureira@example.test", "demo-instituto", "demo-central", "treasurer"),
    ("helena.multipla@example.test", "demo-rede", "demo-aurora", "treasurer"),
    ("helena.multipla@example.test", "demo-rede", "demo-horizonte", "staff"),
    ("helena.multipla@example.test", "demo-instituto", "demo-central", "viewer"),
]


@dataclass
class SeedSummary:
    """How many rows this run created, per table."""

    created: dict[str, int] = field(default_factory=dict)


def seed(connection: Connection, password: str | None = None) -> SeedSummary:
    """Insert the fake data. Idempotent: existing rows (by slug, e-mail or membership) are kept.

    With a `password`, new users can log in with it (each gets its own Argon2id hash); without
    one they have no password and cannot log in until they are given one.
    """
    summary = SeedSummary()

    def count(table: str, rows: int) -> None:
        summary.created[table] = summary.created.get(table, 0) + rows

    for slug, name in ORGANIZATIONS:
        result = connection.execute(
            text(
                "INSERT INTO organizations (name, slug) VALUES (:name, :slug) "
                "ON CONFLICT DO NOTHING"
            ),
            {"name": name, "slug": slug},
        )
        count("organizations", result.rowcount)
    for slug, name, org_slug in SCHOOLS:
        result = connection.execute(
            text(
                "INSERT INTO schools (organization_id, name, slug) "
                "SELECT id, :name, :slug FROM organizations WHERE slug = :org_slug "
                "ON CONFLICT DO NOTHING"
            ),
            {"name": name, "slug": slug, "org_slug": org_slug},
        )
        count("schools", result.rowcount)
    for email, full_name in USERS:
        result = connection.execute(
            text(
                "INSERT INTO users (email, full_name, password_hash) "
                "VALUES (:email, :full_name, :password_hash) ON CONFLICT DO NOTHING"
            ),
            {
                "email": email,
                "full_name": full_name,
                "password_hash": None if password is None else hash_password(password),
            },
        )
        count("users", result.rowcount)
    for email, org_slug, school_slug, role in MEMBERSHIPS:
        result = connection.execute(
            text(
                "INSERT INTO memberships (user_id, organization_id, school_id, role, status) "
                "SELECT u.id, o.id, s.id, :role, 'active' "
                "FROM users u JOIN organizations o ON o.slug = :org_slug "
                "LEFT JOIN schools s ON s.slug = :school_slug AND s.organization_id = o.id "
                "WHERE lower(u.email) = lower(:email) "
                "ON CONFLICT DO NOTHING"
            ),
            {"email": email, "org_slug": org_slug, "school_slug": school_slug, "role": role},
        )
        count("memberships", result.rowcount)
    seed_financial(connection, count)  # categories and demonstration movements (ADR-015)
    return summary


def main() -> int:
    # Checked before anything else, including before the settings are loaded and before any
    # connection is opened.
    if os.environ.get("ENV", "").strip().lower() == "production":
        print(PRODUCTION_REFUSAL, file=sys.stderr)
        return 1
    from app.core.config import get_admin_settings

    settings = get_admin_settings()
    if settings.is_production:
        print(PRODUCTION_REFUSAL, file=sys.stderr)
        return 1
    chosen = os.environ.get("SEED_PASSWORD")
    if chosen is not None and len(chosen) < MIN_LENGTH:
        print(
            f"refusing to seed: SEED_PASSWORD must have at least {MIN_LENGTH} characters",
            file=sys.stderr,
        )
        return 1
    generated = chosen is None
    password = secrets.token_urlsafe(18) if chosen is None else chosen
    engine = create_engine(settings.database_admin_url.get_secret_value())
    try:
        with engine.begin() as connection:
            bypasses_rls: Any = connection.execute(
                text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).scalar_one()
            if not bypasses_rls:
                print(
                    "refusing to seed: the admin role must be able to bypass row level security "
                    "(a development superuser); the application role cannot create tenants.",
                    file=sys.stderr,
                )
                return 1
            summary = seed(connection, password)
    finally:
        engine.dispose()
    for table, created in summary.created.items():
        print(f"{table}: {created} created")
    if generated and summary.created.get("users"):
        # Shown once, and only when users were just created with it; SEED_PASSWORD is never echoed.
        print(f"password of the demo users (shown once): {password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
