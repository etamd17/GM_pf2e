"""Explicit, lazy SQLAlchemy setup for the optional transactional store."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def normalize_database_url(url: str) -> str:
    """Select psycopg 3 for ordinary PostgreSQL/Railway connection URLs.

    Railway commonly exposes ``postgresql://`` while SQLAlchemy interprets
    that unsuffixed scheme as the legacy psycopg2 driver.  PR4A installs
    psycopg 3, so normalization is centralized here instead of relying on each
    command or deploy environment to rewrite the secret.
    """

    if not isinstance(url, str) or not url.strip():
        raise ValueError("database URL is required")
    normalized = url.strip()
    if normalized.startswith("postgres://"):
        return "postgresql+psycopg://" + normalized[len("postgres://") :]
    if normalized.startswith("postgresql://"):
        return "postgresql+psycopg://" + normalized[len("postgresql://") :]
    return normalized


class Database:
    """Own a lazily-created engine and session factory.

    Constructing or importing this class never reads environment variables,
    opens a socket, creates a file, or checks out a connection.  Callers must
    explicitly access :attr:`engine`, open a session, or invoke
    :meth:`create_schema` before SQLAlchemy touches the configured database.
    """

    def __init__(
        self,
        url: str,
        *,
        engine_options: Mapping[str, Any] | None = None,
    ) -> None:
        self.url = normalize_database_url(url)
        self._engine_options = dict(engine_options or {})
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None
        self._initialization_lock = threading.Lock()

    @classmethod
    def from_env(
        cls,
        variable: str = "DATABASE_URL",
        *,
        environ: Mapping[str, str] | None = None,
        engine_options: Mapping[str, Any] | None = None,
    ) -> "Database":
        """Build from an environment mapping when explicitly requested."""

        values = os.environ if environ is None else environ
        raw_url = values.get(variable)
        if not raw_url:
            raise RuntimeError(f"{variable} is not configured")
        return cls(raw_url, engine_options=engine_options)

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            with self._initialization_lock:
                if self._engine is None:
                    options = dict(self._engine_options)
                    options.setdefault("pool_pre_ping", True)
                    engine = create_engine(self.url, **options)
                    if make_url(self.url).get_backend_name() == "sqlite":
                        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
                    self._engine = engine
        return self._engine

    @property
    def session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is None:
            with self._initialization_lock:
                if self._session_factory is None:
                    # Avoid recursively acquiring the same non-reentrant lock.
                    engine = self._engine
                    if engine is None:
                        options = dict(self._engine_options)
                        options.setdefault("pool_pre_ping", True)
                        engine = create_engine(self.url, **options)
                        if make_url(self.url).get_backend_name() == "sqlite":
                            event.listen(
                                engine, "connect", _enable_sqlite_foreign_keys
                            )
                        self._engine = engine
                    self._session_factory = sessionmaker(
                        bind=engine,
                        class_=Session,
                        expire_on_commit=False,
                    )
        return self._session_factory

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Yield a session without implicitly committing caller work."""

        session = self.session_factory()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """Yield a session inside one commit-or-rollback transaction."""

        with self.session_factory.begin() as session:
            yield session

    def create_schema(self) -> None:
        """Create tables explicitly; application startup never calls this."""

        Base.metadata.create_all(self.engine)

    def dispose(self) -> None:
        """Release pooled connections if the engine was initialized."""

        with self._initialization_lock:
            if self._engine is not None:
                self._engine.dispose()
            self._engine = None
            self._session_factory = None


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()
