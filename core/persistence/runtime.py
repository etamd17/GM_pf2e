"""Runtime selection and failure boundaries for ownership persistence.

Importing this module never opens the database. JSON remains the default, shadow
comparisons retain JSON authority, and SQL callers must fail closed when their
store is unavailable. Schema creation and migration remain explicit deploy steps.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm.exc import MultipleResultsFound, NoResultFound

from .database import Database
from .models import Base


logger = logging.getLogger(__name__)
_P = ParamSpec("_P")
_T = TypeVar("_T")
_cached_database: Database | None = None
_cached_url: str | None = None
_database_lock = threading.Lock()
# PR4B uses the unchanged PR4A schema. Update alongside future schema migrations.
_REQUIRED_REVISION = "20260930_0001"


class StoreUnavailable(RuntimeError):
    """The selected ownership store cannot safely serve the operation."""


def backend() -> str:
    """Read the explicit ownership authority; never guess an unknown flag."""

    selected = os.environ.get("OWNERSHIP_BACKEND", "json")
    if selected not in {"json", "shadow", "sql"}:
        raise StoreUnavailable("OWNERSHIP_BACKEND must be json, shadow, or sql")
    return selected


def sql_enabled() -> bool:
    """Whether SQL is authoritative, rather than a shadow comparison source."""

    return backend() == "sql"


def database() -> Database:
    """Return the lazy database for the current URL without migrating it.

    Adapters use this function at operation time, allowing tests to inject a
    prepared ``Database`` by replacing this callable. A changed URL receives a
    fresh pool; no credential or URL is included in outward-facing errors.
    """

    global _cached_database, _cached_url
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise StoreUnavailable("DATABASE_URL is required for ownership persistence")
    with _database_lock:
        if _cached_database is None or _cached_url != url:
            try:
                replacement = Database(url)
            except (SQLAlchemyError, ValueError, OSError):
                raise StoreUnavailable("Ownership database configuration is invalid") from None
            previous = _cached_database
            _cached_database = replacement
            _cached_url = url
            if previous is not None:
                previous.dispose()
        return _cached_database


def store_call(function: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs) -> _T:
    """Hide database diagnostics while preserving expected domain rejections.

    Constraint/data conflicts belong to the adapter's validation handling, as do
    domain ``ValueError`` and persistence service errors. Unexpected database or
    operating-system failures must never trigger a fallback to JSON in SQL mode.
    """

    try:
        return function(*args, **kwargs)
    except (IntegrityError, DataError, NoResultFound, MultipleResultsFound):
        raise
    except (SQLAlchemyError, OSError):
        raise StoreUnavailable("Ownership database is unavailable") from None


def require_ready() -> None:
    """Verify the configured schema with read-only queries, never DDL.

    Checking all mapped columns catches partially migrated databases as well as
    missing tables. Metadata-created test databases have no Alembic marker; if a
    marker is present, it must identify the migration required by this release.
    """

    if backend() == "json":
        return

    def check_schema() -> None:
        with database().engine.connect() as connection:
            for table in Base.metadata.sorted_tables:
                connection.execute(select(table).limit(0)).close()
            if inspect(connection).has_table("alembic_version"):
                revisions = set(
                    connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalars()
                )
                if revisions != {_REQUIRED_REVISION}:
                    raise StoreUnavailable("Ownership database schema is not ready")

    store_call(check_schema)


def compare_shadow(
    operation: str, legacy_value: _T, sql_reader: Callable[[], object]
) -> _T:
    """Observe a SQL read without changing JSON authority or writing either store.

    Readers must be side-effect-free. Mismatch diagnostics include only a static
    operation label, never compared values, exceptions, SQL, or connection URLs.
    """

    if backend() != "shadow":
        return legacy_value
    operation_label = re.sub(r"[^a-zA-Z0-9_.:-]", "_", operation)[:80]
    try:
        matched = sql_reader() == legacy_value
    except Exception:
        logger.warning("ownership_shadow_unavailable operation=%s", operation_label)
        return legacy_value
    if not matched:
        logger.warning("ownership_shadow_mismatch operation=%s", operation_label)
    return legacy_value
