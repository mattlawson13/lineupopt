from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.settings import get_settings


def _make_engine():
    settings = get_settings()
    is_sqlite = settings.database_url.startswith("sqlite")
    connect_args = {}
    if is_sqlite:
        # `timeout` makes sqlite3 itself retry for up to 30s instead of
        # immediately raising "database is locked" under any concurrent
        # access (e.g. a slate build's writes overlapping a health check
        # or another request) — the default is 5s, too short for a build
        # that holds the connection through a multi-step pipeline.
        connect_args = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(settings.database_url, connect_args=connect_args, future=True)

    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            # WAL mode allows concurrent readers alongside a single writer
            # instead of SQLite's default exclusive-lock-per-write
            # behavior, which is what was actually causing the lock
            # contention (sqlite is a real fallback for small/dev
            # deployments, but this is required to keep it usable under
            # even light concurrent request load — e.g. Render's default
            # setup, or Postgres being the real fix long-term).
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    """Dev/test convenience — production should use Alembic migrations."""
    from app.db.base import Base
    import app.models  # noqa: F401  (ensure all models are registered)

    Base.metadata.create_all(bind=engine)
