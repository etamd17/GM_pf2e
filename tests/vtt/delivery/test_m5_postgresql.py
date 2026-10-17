"""M5 import concurrency that only PostgreSQL can prove (handoff 048 finding 2):
the importer takes the campaign stream lock before touching any destination
row, exactly as a command does, so an import and a scene edit cannot deadlock,
and two simultaneous first imports of one scene serialize. Run with
DATABASE_URL pointing at a disposable PostgreSQL database."""

from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import select, text

from core.persistence import Base, Database
from vtt.importer import legacy_scenes as importer
from vtt.storage import stream
from vtt.storage.models import VttEntity, VttImportedEntity, VttScene

from .conftest import POSTGRES_URL, seed_identity
from .test_m5_import import full_scene

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.skipif(not POSTGRES_URL.startswith(("postgresql", "postgres")),
                       reason="needs DATABASE_URL for a disposable PostgreSQL database"),
]


@pytest.fixture
def pg():
    db = Database(POSTGRES_URL)
    Base.metadata.drop_all(db.engine)
    Base.metadata.create_all(db.engine)
    seed_identity(db)
    try:
        yield db
    finally:
        Base.metadata.drop_all(db.engine)
        db.dispose()


def test_an_import_waits_for_the_stream_lock_before_touching_the_scene(pg):
    """A command's order: stream lock, then the scene row. With the old order
    (scene row, then stream) this interleaving deadlocks; now the import holds
    nothing while it waits, so the command's scene update goes straight through."""
    importer.import_campaign(pg, "camp_a", [full_scene()], None, apply=True)
    holder = pg.session_factory()
    holder.begin()
    holder.execute(text("SET LOCAL lock_timeout = '3s'"))
    stream.lock_stream(holder, "camp_a")                      # the command has the stream...
    outcome: dict = {}

    def reimport():
        try:
            changed = full_scene()
            changed["name"] = "Reimported"
            outcome["report"] = importer.import_campaign(pg, "camp_a", [changed], None, apply=True)
        except Exception as exc:  # pragma: no cover - the failure being guarded
            outcome["error"] = exc

    worker = threading.Thread(target=reimport)
    worker.start()
    time.sleep(0.5)                                            # ...the import is now waiting
    started = time.monotonic()
    scene = holder.execute(select(VttScene).where(VttScene.id == "legacy_abc123").with_for_update()).scalar_one()
    scene.name = "Edited by the GM"                            # ...and the command takes the scene row
    holder.flush()
    assert time.monotonic() - started < 2, "the scene row was held by the waiting import"
    holder.commit()
    holder.close()
    worker.join(timeout=15)
    assert "error" not in outcome, outcome.get("error")
    assert outcome["report"]["scenes"][0]["status"] == "imported"
    with pg.session() as session:                              # the import ran after the edit
        assert session.get(VttScene, ("camp_a", "legacy_abc123")).name == "Reimported"


def test_two_simultaneous_first_imports_serialize(pg):
    results: list = [None, None]
    gate = threading.Barrier(2)

    def run(index):
        gate.wait()
        try:
            results[index] = importer.import_campaign(pg, "camp_a", [full_scene()], None, apply=True)
        except Exception as exc:  # pragma: no cover - the failure being guarded
            results[index] = exc

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    for result in results:
        assert not isinstance(result, Exception), result
        assert result["scenes"][0]["status"] == "imported"
    created = sorted(r["scenes"][0]["created"] for r in results)
    assert created == [0, 8]                                   # one created, the other a proven re-import
    with pg.session() as session:
        assert len(session.execute(select(VttEntity)).scalars().all()) == 8
        assert len(session.execute(select(VttImportedEntity)).scalars().all()) == 8
