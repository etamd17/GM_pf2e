from __future__ import annotations

import logging

import pytest
from sqlalchemy import event, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError

from core.persistence import LastGameMasterError
from core.persistence import runtime


@pytest.fixture(autouse=True)
def runtime_configuration(monkeypatch):
    monkeypatch.delenv("OWNERSHIP_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(runtime, "_cached_database", None)
    monkeypatch.setattr(runtime, "_cached_url", None)
    yield
    if runtime._cached_database is not None:
        runtime._cached_database.dispose()


def test_default_json_backend_never_opens_database(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("JSON mode must not access the database")

    monkeypatch.setattr(runtime, "Database", forbidden)
    assert runtime.backend() == "json"
    assert not runtime.sql_enabled()
    runtime.require_ready()
    legacy = {"allowed": True}
    assert runtime.compare_shadow("membership.read", legacy, forbidden) is legacy


@pytest.mark.parametrize("selected", ["json", "shadow", "sql"])
def test_backend_selection(monkeypatch, selected):
    monkeypatch.setenv("OWNERSHIP_BACKEND", selected)
    assert runtime.backend() == selected
    assert runtime.sql_enabled() is (selected == "sql")


def test_invalid_backend_fails_closed_without_echoing_configuration(monkeypatch):
    monkeypatch.setenv("OWNERSHIP_BACKEND", "password-do-not-log")
    with pytest.raises(runtime.StoreUnavailable) as raised:
        runtime.backend()
    assert "password-do-not-log" not in str(raised.value)
    with pytest.raises(runtime.StoreUnavailable):
        runtime.require_ready()


@pytest.mark.parametrize("selected", ["shadow", "sql"])
def test_sql_dependent_modes_require_database_url(monkeypatch, selected):
    monkeypatch.setenv("OWNERSHIP_BACKEND", selected)
    with pytest.raises(runtime.StoreUnavailable, match="DATABASE_URL"):
        runtime.database()
    with pytest.raises(runtime.StoreUnavailable, match="DATABASE_URL"):
        runtime.require_ready()


def test_database_creation_is_lazy_and_cached_by_current_url(monkeypatch, tmp_path):
    first_path = tmp_path / "first.sqlite"
    second_path = tmp_path / "second.sqlite"
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///" + str(first_path))
    first = runtime.database()
    assert runtime.database() is first
    assert not first_path.exists()

    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///" + str(second_path))
    second = runtime.database()
    assert second is not first
    assert runtime.database() is second
    assert not second_path.exists()


@pytest.mark.parametrize("selected", ["shadow", "sql"])
def test_missing_schema_fails_closed_without_creating_tables(monkeypatch, selected):
    monkeypatch.setenv("OWNERSHIP_BACKEND", selected)
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    database = runtime.database()
    with pytest.raises(runtime.StoreUnavailable):
        runtime.require_ready()
    assert inspect(database.engine).get_table_names() == []


def test_metadata_created_schema_is_ready_with_read_only_queries(
    monkeypatch, sqlite_database
):
    monkeypatch.setenv("OWNERSHIP_BACKEND", "sql")
    monkeypatch.setattr(runtime, "database", lambda: sqlite_database)
    statements = []
    event.listen(
        sqlite_database.engine,
        "before_cursor_execute",
        lambda conn, cursor, statement, parameters, context, executemany:
            statements.append(statement),
    )
    runtime.require_ready()
    assert statements
    assert all(statement.lstrip().upper().startswith(("SELECT", "PRAGMA"))
               for statement in statements)


def test_incomplete_columns_fail_readiness(monkeypatch):
    monkeypatch.setenv("OWNERSHIP_BACKEND", "sql")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    database = runtime.database()
    database.create_schema()
    with database.engine.begin() as connection:
        connection.execute(text("ALTER TABLE users DROP COLUMN password_hash"))
    with pytest.raises(runtime.StoreUnavailable):
        runtime.require_ready()


@pytest.mark.parametrize("revision", ["20261001_0002", "20260930_0001", "wrong-revision"])
def test_migration_revision_is_checked_when_present(
    monkeypatch, sqlite_database, revision
):
    monkeypatch.setenv("OWNERSHIP_BACKEND", "sql")
    monkeypatch.setattr(runtime, "database", lambda: sqlite_database)
    with sqlite_database.engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
        connection.execute(
            text("INSERT INTO alembic_version VALUES (:revision)"),
            {"revision": revision},
        )
    if revision == "20261001_0002":
        runtime.require_ready()
    else:
        with pytest.raises(runtime.StoreUnavailable):
            runtime.require_ready()


@pytest.mark.parametrize("selected", ["json", "sql"])
def test_shadow_reader_does_not_run_in_other_modes(monkeypatch, selected):
    monkeypatch.setenv("OWNERSHIP_BACKEND", selected)

    def forbidden():
        pytest.fail("Only shadow mode runs comparison readers")

    assert runtime.compare_shadow("user.read", "legacy", forbidden) == "legacy"


def test_shadow_mismatch_keeps_legacy_authority_and_logs_no_payloads(
    monkeypatch, caplog
):
    monkeypatch.setenv("OWNERSHIP_BACKEND", "shadow")
    legacy = {"password_hash": "legacy-secret", "allowed": True}
    with caplog.at_level(logging.WARNING):
        result = runtime.compare_shadow(
            "user.read", legacy,
            lambda: {"password_hash": "sql-secret", "allowed": False},
        )
    assert result is legacy
    assert "mismatch" in caplog.text
    assert "user.read" in caplog.text
    assert "legacy-secret" not in caplog.text
    assert "sql-secret" not in caplog.text
    assert "password_hash" not in caplog.text


def test_matching_shadow_read_does_not_warn(monkeypatch, caplog):
    monkeypatch.setenv("OWNERSHIP_BACKEND", "shadow")
    with caplog.at_level(logging.WARNING):
        assert runtime.compare_shadow("user.read", {"id": 1}, lambda: {"id": 1}) == {"id": 1}
    assert caplog.records == []


def test_shadow_unavailability_is_sanitized_and_keeps_legacy_authority(
    monkeypatch, caplog
):
    monkeypatch.setenv("OWNERSHIP_BACKEND", "shadow")

    def unavailable():
        raise OperationalError("SELECT secret", {"secret": "private"},
                               OSError("postgresql://user:password@host/db"))

    with caplog.at_level(logging.WARNING):
        assert runtime.compare_shadow("user.read\nforged-log", False, unavailable) is False
    assert "unavailable" in caplog.text
    assert "user.read_forged-log" in caplog.text
    assert "password" not in caplog.text
    assert "private" not in caplog.text
    assert "SELECT" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_store_call_returns_results_and_forwards_arguments():
    assert runtime.store_call(lambda x, *, y: x + y, 2, y=3) == 5


@pytest.mark.parametrize("error", [
    OSError("credential-in-connection-error"),
    OperationalError("secret-query", {}, OSError("secret-password")),
])
def test_store_call_sanitizes_database_failures(error):
    def failed():
        raise error

    with pytest.raises(runtime.StoreUnavailable) as raised:
        runtime.store_call(failed)
    assert "secret" not in str(raised.value)
    assert "credential" not in str(raised.value)
    assert raised.value.__suppress_context__


@pytest.mark.parametrize("error", [
    ValueError("invalid username"),
    LastGameMasterError("last GM cannot leave"),
    IntegrityError("duplicate username", {}, ValueError("unique")),
])
def test_store_call_preserves_domain_validation_errors(error):
    def rejected():
        raise error

    with pytest.raises(type(error)) as raised:
        runtime.store_call(rejected)
    assert raised.value is error
