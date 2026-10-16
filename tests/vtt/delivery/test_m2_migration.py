"""Migration 20261016_0004: campaign-scoped keys keep every row, on SQLite's
table rebuild as well as PostgreSQL's in-place change (the PostgreSQL run is
recorded in the M2 handoff), and the receipts table matches the model."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[3]


def alembic(url: str, *args: str) -> str:
    env = dict(os.environ, DATABASE_URL=url)
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout


def counts(engine):
    with engine.connect() as c:
        return (c.execute(text("select count(*) from vtt_scenes")).scalar(),
                c.execute(text("select count(*) from vtt_entities")).scalar(),
                c.execute(text("select scene_id from vtt_table_pointers")).scalar())


def test_rekeying_keeps_scenes_entities_and_the_table_pointer_on_sqlite(tmp_path):
    url = f"sqlite+pysqlite:///{(tmp_path / 'm.sqlite').as_posix()}"
    alembic(url, "upgrade", "20261016_0003")
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            c.execute(text("PRAGMA foreign_keys=ON"))
            c.execute(text("insert into campaigns(id,slug,name,system,created_at,session_number,settings,"
                           "source_payload) values ('camp_a','a','A','pf2e','2026-10-16',1,'{}','{}')"))
            c.execute(text("insert into vtt_scenes(id,campaign_id,name,width,height,grid_size,show_grid,published,"
                           "created_at,updated_at) values ('s1','camp_a','S',64,64,8,1,1,'2026-10-16','2026-10-16')"))
            c.execute(text("insert into vtt_entities(campaign_id,scene_id,kind,id,hidden,data,updated_at) "
                           "values ('camp_a','s1','token','t1',0,:d,'2026-10-16')"), {"d": '{"x": 1, "y": 2}'})
            c.execute(text("insert into vtt_table_pointers(campaign_id,scene_id) values ('camp_a','s1')"))
        assert counts(engine) == (1, 1, "s1")
        engine.dispose()
        alembic(url, "upgrade", "head")
        assert counts(engine) == (1, 1, "s1")
        pk = inspect(engine).get_pk_constraint
        assert pk("vtt_scenes")["constrained_columns"] == ["campaign_id", "id"]
        assert pk("vtt_entities")["constrained_columns"] == ["campaign_id", "scene_id", "kind", "id"]
        engine.dispose()
        alembic(url, "downgrade", "20261016_0003")
        assert counts(engine) == (1, 1, "s1")
        assert inspect(engine).get_pk_constraint("vtt_scenes")["constrained_columns"] == ["id"]
    finally:
        engine.dispose()


def test_offline_upgrade_names_every_receipt_check_constraint_once():
    env = dict(os.environ, DATABASE_URL="postgresql+psycopg://offline:offline@localhost/offline")
    sql = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "20261016_0003:20261016_0004", "--sql"],
                         cwd=ROOT, env=env, check=True, capture_output=True, text=True, encoding="utf-8").stdout
    for name in ("ck_vtt_command_receipts_known_principal_kind",
                 "ck_vtt_command_receipts_outcome_code_consistent", "uq_vtt_command_receipts_key"):
        assert sql.count(name) == 1, name
    assert "PRIMARY KEY (campaign_id, id)" in sql
    assert "PRIMARY KEY (campaign_id, scene_id, kind, id)" in sql
