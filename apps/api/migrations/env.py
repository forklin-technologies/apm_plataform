from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app import models  # noqa: F401  (registers every model on Base.metadata)
from app.core.config import get_admin_settings
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    # `alembic upgrade head --sql` prints SQL only. Nothing secret is rendered: the password of
    # the application role is set by an online-only step (versions/0002_tenancy_roles.py).
    context.configure(
        url=get_admin_settings().database_admin_url.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Migrations run as the admin (DATABASE_ADMIN_URL), never as the application role.
    engine = create_engine(
        get_admin_settings().database_admin_url.get_secret_value(),
        poolclass=pool.NullPool,
    )
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
