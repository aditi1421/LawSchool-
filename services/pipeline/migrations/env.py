"""Alembic environment.

The URL comes from pipeline.db.engine (DATABASE_URL), not alembic.ini — so
migrations always target the same database the app does, in dev, CI and
production alike.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import text

from pipeline.db.engine import database_url, get_engine
from pipeline.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = get_engine()
    with connectable.connect() as connection:
        # Vector columns need the extension present before the DDL runs.
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
