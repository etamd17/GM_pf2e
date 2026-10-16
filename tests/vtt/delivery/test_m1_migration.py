"""Migration 20261016_0003 matches the VTT models exactly."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from core.persistence import Base

ROOT = Path(__file__).resolve().parents[3]


def test_upgrade_head_produces_exactly_the_vtt_models(tmp_path):
    url = f"sqlite+pysqlite:///{(tmp_path / 'm.sqlite').as_posix()}"
    env = dict(os.environ, DATABASE_URL=url)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True,
                   capture_output=True, text=True, encoding="utf-8")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            diff = [d for d in compare_metadata(context, Base.metadata)
                    if "vtt_" in repr(d)]
    finally:
        engine.dispose()
    assert diff == [], diff
    # and the migrated schema takes real writes (the outbox id must autoincrement)
    from core.persistence import Database
    from vtt.storage import writes as W
    from .conftest import seed_identity
    db = Database(url)
    try:
        seed_identity(db)
        assert W.create_scene(db, "camp_a", "scene_a", name="A") == 1
        assert W.upsert_entity(db, "camp_a", "scene_a", "token", "t1", {"x": 1, "y": 1}) == 2
    finally:
        db.dispose()


def test_offline_upgrade_names_every_vtt_check_constraint_once():
    env = dict(os.environ, DATABASE_URL="postgresql+psycopg://offline:offline@localhost/offline")
    sql = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "20261001_0002:20261016_0003", "--sql"],
                         cwd=ROOT, env=env, check=True, capture_output=True, text=True,
                         encoding="utf-8").stdout
    for name in ("ck_vtt_campaign_streams_position_nonnegative", "ck_vtt_scenes_width_bounded",
                 "ck_vtt_entities_known_kind", "ck_vtt_outbox_position_positive",
                 "ck_vtt_table_grants_epoch_nonnegative"):
        assert sql.count(name) == 1, name
    assert "REPEATABLE" not in sql            # isolation is a runtime choice, not schema
