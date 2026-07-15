"""Engine and session management.

DATABASE_URL points at local Docker Postgres by default (see
docker-compose.yml). In production it points at managed Postgres in an India
region — case files are privileged and DPDP Act 2023 applies, so data
residency is a deployment requirement, not a preference.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_URL = "postgresql+psycopg://lawschool:lawschool@localhost:5433/lawschool"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(database_url(), pool_pre_ping=True, future=True)


@lru_cache(maxsize=1)
def _sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


def get_session() -> Session:
    return _sessionmaker()()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on error."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ensure_extensions(engine: Engine | None = None) -> None:
    """pgvector must exist before the schema that uses Vector columns."""
    eng = engine or get_engine()
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


def reset_engine_cache() -> None:
    """Drop cached engine/sessionmaker — used by tests that swap DATABASE_URL."""
    get_engine.cache_clear()
    _sessionmaker.cache_clear()
