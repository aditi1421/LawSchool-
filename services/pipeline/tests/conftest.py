"""Shared fixtures.

Tests that touch matters need Postgres — they run against the same engine and
DDL production uses, because a repository mocked away from its database proves
nothing about the schema. They skip cleanly when no database is reachable, so
`pytest` still works offline; `docker compose up -d` turns them on.

These tests drop and recreate the schema, so they must NEVER run against a
database holding real matters. This module forces DATABASE_URL onto a
dedicated `<db>_test` database before the app's engine is ever built, creating
it if needed, and refuses to run if that redirect did not take effect.
"""

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

TEST_DB_SUFFIX = "_test"


def _redirect_to_test_database() -> str | None:
    """Point DATABASE_URL at a throwaway database and create it if absent.

    Returns the test URL, or None when no server is reachable.
    """
    from pipeline.db.engine import DEFAULT_URL, reset_engine_cache

    url = make_url(os.environ.get("DATABASE_URL", DEFAULT_URL))
    if url.database and url.database.endswith(TEST_DB_SUFFIX):
        test_url = url
    else:
        test_url = url.set(database=f"{url.database}{TEST_DB_SUFFIX}")

    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"),
                {"n": test_url.database},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{test_url.database}"'))
    except Exception:
        return None
    finally:
        admin.dispose()

    os.environ["DATABASE_URL"] = test_url.render_as_string(hide_password=False)
    reset_engine_cache()  # any engine built from the old URL must be discarded
    return os.environ["DATABASE_URL"]


_TEST_URL = _redirect_to_test_database()


def db_available() -> bool:
    if _TEST_URL is None:
        return False
    from pipeline.db.engine import get_engine

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _assert_test_database() -> None:
    """Refuse to drop a schema that is not a *_test one.

    This lives with the drop, not on every test: the danger is `drop_all`, and
    an autouse guard also failed pure tests (no database involved) whenever
    Postgres simply was not running — which is a normal offline state and
    should skip DB tests, not break the suite.
    """
    from pipeline.db.engine import database_url

    name = make_url(database_url()).database or ""
    assert name.endswith(TEST_DB_SUFFIX), (
        f"tests are pointed at database {name!r}, which is not a *{TEST_DB_SUFFIX} "
        "database — refusing to run because these tests drop the schema"
    )


requires_db = pytest.mark.skipif(
    not db_available(), reason="Postgres not reachable — run `docker compose up -d`"
)


@pytest.fixture
def clean_db():
    """A fresh schema per test — no cross-test bleed. Test database only."""
    from pipeline.db.engine import get_engine
    from pipeline.db.models import Base

    _assert_test_database()  # never drop a schema holding real matters
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def repo(clean_db, tmp_path: Path):
    """Repository on a clean schema with local blob storage and no embedder."""
    from pipeline.db.repository import MatterRepository
    from pipeline.storage import LocalStorage

    return MatterRepository(storage=LocalStorage(tmp_path / "blobs"), embedder=None)


@pytest.fixture
def api_client(clean_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """TestClient wired to the clean schema and a temp blob root.

    get_store() is lru_cached, so the cache must be cleared or the client would
    reuse a repository pointed at a previous test's storage.
    """
    from fastapi.testclient import TestClient

    from pipeline.api import app, get_store

    monkeypatch.setenv("LAWSCHOOL_DATA_DIR", str(tmp_path / "blobs"))
    monkeypatch.setenv("LAWSCHOOL_EMBEDDINGS", "none")
    get_store.cache_clear()
    yield TestClient(app)
    get_store.cache_clear()
