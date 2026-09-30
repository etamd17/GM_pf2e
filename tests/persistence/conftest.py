from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool

from core.persistence import Database


@pytest.fixture
def sqlite_database():
    database = Database(
        "sqlite+pysqlite:///:memory:",
        engine_options={
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        },
    )
    database.create_schema()
    try:
        yield database
    finally:
        database.dispose()
