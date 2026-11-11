"""PE-12: transactional character/encounter state schema (PE-01 sections 1, 2, 4, 7).

Model-level cases run against ``Base.metadata`` on SQLite (and, for the
PostgreSQL-marked cases, against an isolated PostgreSQL schema).  Migration
cases drive the real Alembic revisions in a subprocess, because the Alembic
environment configures logging globally and must not leak into other tests.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from core.persistence import (
    Campaign,
    CampaignClock,
    CampaignMembership,
    CampaignSession,
    CampaignStateAuthority,
    Character,
    CharacterAssignment,
    CharacterState,
    Database,
    EncounterCombatant,
    EncounterState,
    MigrationRun,
    MigrationRunRepository,
    User,
    normalize_database_url,
)
from core.persistence import runtime


REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
PIN = {"ruleset_id": "pf2e-legacy-unknown", "manifest_sha256": None}
HEAD = runtime._REQUIRED_REVISION
PREVIOUS = "20261001_0002"


# --------------------------------------------------------------------------
# helpers


def _user(user_id):
    return User(id=user_id, username=user_id, normalized_username=user_id,
                display_name=user_id, password_hash="hash", created_at=NOW)


def _campaign(campaign_id, *, system="pf2e"):
    return Campaign(id=campaign_id, slug=campaign_id, name=campaign_id,
                    system=system, created_by_user_id="gm", created_at=NOW)


def _character(character_id, campaign_id, *, archived_at=None, legacy_file=None):
    return Character(
        id=character_id, campaign_id=campaign_id, system="pf2e",
        display_name=character_id, legacy_storage="party_data",
        legacy_file=legacy_file or character_id + ".json",
        content_checksum="0" * 64, created_at=NOW, updated_at=NOW,
        archived_at=archived_at,
    )


def _state(campaign_id, character_id, domain, *, revision=0, payload=None):
    return CharacterState(
        campaign_id=campaign_id, character_id=character_id, domain=domain,
        schema_version=1, revision=revision, rules_profile="pf2e",
        content=dict(PIN), payload=payload if payload is not None else {},
    )


def _encounter(campaign_id, encounter_id, *, status="active"):
    return EncounterState(
        campaign_id=campaign_id, encounter_id=encounter_id, status=status,
        round=1, turn=0, initiative=[], payload={}, clock_anchors={},
        rules_profile="pf2e", content=dict(PIN), schema_version=1, revision=0,
    )


def _seed(database, *campaigns):
    with database.transaction() as session:
        session.add(_user("gm"))
        session.flush()
        for campaign_id in campaigns:
            session.add(_campaign(campaign_id))
        session.flush()
        for campaign_id in campaigns:
            session.add(_character("pc-" + campaign_id, campaign_id))


def _refused(database, *rows, match):
    """Insert ``rows`` and require the refusal to come from ``match``.

    ``match`` is a regex searched in the driver's own message only (not the
    SQL statement SQLAlchemy appends).  SQLite names the CHECK constraint and
    lists the columns of a violated unique key, so those refusals are pinned
    by the message.  SQLite reports every foreign-key failure with the same
    text, so an FK refusal is pinned only by its fixture: each such row
    breaks exactly one foreign key.  PostgreSQL names every constraint and
    index.
    """
    with pytest.raises(IntegrityError) as caught, database.transaction() as session:
        for row in rows:
            session.add(row)
            session.flush()
    message = str(caught.value.orig)
    assert re.search(match, message), message


def _check(name):
    return r"CHECK constraint failed: %s$" % re.escape(name)


def _unique(table, *columns):
    return r"UNIQUE constraint failed: %s$" % re.escape(
        ", ".join(table + "." + column for column in columns))


_FOREIGN_KEY = r"^FOREIGN KEY constraint failed$"


def _pg(name):
    return r'constraint "%s"' % re.escape(name)


# --------------------------------------------------------------------------
# character state (section 2)


def test_build_and_play_round_trip_with_independent_revisions(sqlite_database):
    _seed(sqlite_database, "c1")
    with sqlite_database.transaction() as session:
        session.add_all([
            _state("c1", "pc-c1", "build", revision=3, payload={"name": "Ana"}),
            _state("c1", "pc-c1", "play", revision=21, payload={"hp": 24}),
        ])
    with sqlite_database.transaction() as session:
        play = session.get(CharacterState, ("c1", "pc-c1", "play"))
        play.payload = {"hp": 20}
        play.revision += 1
    with sqlite_database.session() as session:
        rows = {row.domain: row for row in session.scalars(select(CharacterState))}
    assert rows["build"].revision == 3 and rows["build"].payload == {"name": "Ana"}
    assert rows["play"].revision == 22 and rows["play"].payload == {"hp": 20}
    assert rows["play"].rules_profile == "pf2e" and rows["play"].content == PIN


@pytest.mark.parametrize("change, constraint", [
    ({"revision": -1}, "revision_nonnegative"),
    ({"domain": "inventory"}, "known_domain"),
    ({"rules_profile": "cosmere"}, "known_rules_profile"),
    ({"schema_version": 0}, "schema_version_positive"),
])
def test_character_state_rejects_invalid_values(sqlite_database, change, constraint):
    _seed(sqlite_database, "c1")
    row = _state("c1", "pc-c1", "play")
    for key, value in change.items():
        setattr(row, key, value)
    _refused(sqlite_database, row, match=_check("ck_character_states_" + constraint))


def test_character_state_key_is_unique_and_campaign_scoped(sqlite_database):
    _seed(sqlite_database, "c1", "c2")
    with sqlite_database.transaction() as session:
        session.add(_state("c1", "pc-c1", "play"))
    _refused(sqlite_database, _state("c1", "pc-c1", "play"),
             match=_unique("character_states", "campaign_id", "character_id", "domain"))
    # The composite FK refuses a character that belongs to another campaign.
    _refused(sqlite_database, _state("c1", "pc-c2", "play"), match=_FOREIGN_KEY)


def test_state_rows_cascade_with_their_character(sqlite_database):
    _seed(sqlite_database, "c1")
    with sqlite_database.transaction() as session:
        session.add_all([_state("c1", "pc-c1", "build"), _state("c1", "pc-c1", "play"),
                         _encounter("c1", "e1")])
        session.flush()
        session.add(EncounterCombatant(campaign_id="c1", encounter_id="e1",
                                       combatant_id="k1", kind="pc", character_id="pc-c1"))
    with sqlite_database.transaction() as session:
        session.delete(session.get(Character, "pc-c1"))
    with sqlite_database.session() as session:
        assert session.scalars(select(CharacterState)).all() == []
        assert session.scalars(select(EncounterCombatant)).all() == []
        assert session.get(EncounterState, ("c1", "e1")) is not None


def test_two_instances_do_not_share_mutable_defaults(sqlite_database):
    _seed(sqlite_database, "c1")
    with sqlite_database.transaction() as session:
        first = CharacterState(campaign_id="c1", character_id="pc-c1", domain="build",
                               schema_version=1, revision=0, rules_profile="pf2e")
        second = CharacterState(campaign_id="c1", character_id="pc-c1", domain="play",
                                schema_version=1, revision=0, rules_profile="pf2e")
        session.add_all([first, second])
        session.flush()
        first.payload["hp"] = 1
        first.content["x"] = 1
        assert second.payload == {} and second.content == {}
        first_encounter = EncounterState(campaign_id="c1", encounter_id="e1", status="ended",
                                         rules_profile="pf2e", schema_version=1)
        second_encounter = EncounterState(campaign_id="c1", encounter_id="e2", status="ended",
                                          rules_profile="pf2e", schema_version=1)
        session.add_all([first_encounter, second_encounter])
        session.flush()
        first_encounter.initiative.append("k1")
        first_encounter.clock_anchors["world"] = 1
        assert second_encounter.initiative == [] and second_encounter.clock_anchors == {}


# --------------------------------------------------------------------------
# encounters (section 2)


def test_encounter_and_combatants_round_trip(sqlite_database):
    _seed(sqlite_database, "c1")
    with sqlite_database.transaction() as session:
        encounter = _encounter("c1", "e1")
        encounter.initiative = ["k1", "k2"]
        encounter.payload = {"creatures": {"k2": {"hp": 30}}}
        encounter.clock_anchors = {"world": 600}
        session.add(encounter)
        session.flush()
        session.add_all([
            EncounterCombatant(campaign_id="c1", encounter_id="e1", combatant_id="k1",
                               kind="pc", character_id="pc-c1"),
            EncounterCombatant(campaign_id="c1", encounter_id="e1", combatant_id="k2",
                               kind="creature", creature_ref="bestiary/goblin-warrior",
                               legacy_ref="instance-7"),
        ])
    with sqlite_database.session() as session:
        stored = session.get(EncounterState, ("c1", "e1"))
        combatants = session.scalars(select(EncounterCombatant)
                                     .order_by(EncounterCombatant.combatant_id)).all()
    assert stored.initiative == ["k1", "k2"] and stored.round == 1 and stored.turn == 0
    assert stored.payload["creatures"]["k2"]["hp"] == 30
    assert [c.kind for c in combatants] == ["pc", "creature"]
    assert combatants[1].legacy_ref == "instance-7"


def test_one_active_encounter_per_campaign(sqlite_database):
    _seed(sqlite_database, "c1", "c2")
    with sqlite_database.transaction() as session:
        session.add_all([_encounter("c1", "e1"), _encounter("c1", "old", status="ended"),
                         _encounter("c1", "older", status="ended"), _encounter("c2", "e9")])
    _refused(sqlite_database, _encounter("c1", "e2"),
             match=_unique("encounter_states", "campaign_id"))
    _refused(sqlite_database, _encounter("c1", "e3", status="paused"),
             match=_check("ck_encounter_states_known_status"))


@pytest.mark.parametrize("change, constraint", [
    ({"round": -1}, "round_nonnegative"), ({"turn": -1}, "turn_nonnegative"),
    ({"revision": -1}, "revision_nonnegative"),
])
def test_encounter_rejects_negative_counters(sqlite_database, change, constraint):
    _seed(sqlite_database, "c1")
    row = _encounter("c1", "e1")
    for key, value in change.items():
        setattr(row, key, value)
    _refused(sqlite_database, row, match=_check("ck_encounter_states_" + constraint))


def test_combatant_cannot_reference_another_campaigns_character(sqlite_database):
    _seed(sqlite_database, "c1", "c2")
    with sqlite_database.transaction() as session:
        session.add(_encounter("c1", "e1"))
    _refused(sqlite_database, EncounterCombatant(
        campaign_id="c1", encounter_id="e1", combatant_id="k1", kind="pc",
        character_id="pc-c2"), match=_FOREIGN_KEY)
    # A PC combatant must name its character.
    _refused(sqlite_database, EncounterCombatant(
        campaign_id="c1", encounter_id="e1", combatant_id="k2", kind="pc"),
        match=_check("ck_encounter_combatants_pc_has_character"))
    # A combatant must belong to an encounter of the same campaign.
    _refused(sqlite_database, EncounterCombatant(
        campaign_id="c2", encounter_id="e1", combatant_id="k3", kind="creature"),
        match=_FOREIGN_KEY)


# --------------------------------------------------------------------------
# clocks, sessions and authority (sections 1, 4, 5, 7)


def test_campaign_clocks(sqlite_database):
    _seed(sqlite_database, "c1", "c2")
    with sqlite_database.transaction() as session:
        session.add_all([CampaignClock(campaign_id="c1", clock="world", value=600, revision=2),
                         CampaignClock(campaign_id="c1", clock="downtime", value=3, revision=0)])
    _refused(sqlite_database, CampaignClock(campaign_id="c1", clock="world", value=1, revision=0),
             match=_unique("campaign_clocks", "campaign_id", "clock"))
    _refused(sqlite_database, CampaignClock(campaign_id="c1", clock="round", value=1, revision=0),
             match=_check("ck_campaign_clocks_known_clock"))
    # c2 has no clocks yet, so only the value can be wrong here.
    _refused(sqlite_database, CampaignClock(campaign_id="c2", clock="downtime", value=-1,
                                            revision=0),
             match=_check("ck_campaign_clocks_value_nonnegative"))
    _refused(sqlite_database, CampaignClock(campaign_id="c2", clock="world", value=0,
                                            revision=-1),
             match=_check("ck_campaign_clocks_revision_nonnegative"))


def _session(campaign_id, session_id, status="active", **extra):
    values = {"started_at": NOW, "started_by": "gm", "origin": "website"}
    values.update(extra)
    return CampaignSession(campaign_id=campaign_id, session_id=session_id, status=status,
                           **values)


def test_one_active_session_per_campaign_and_ids_never_reused(sqlite_database):
    _seed(sqlite_database, "c1", "c2", "c3")
    with sqlite_database.transaction() as session:
        session.add_all([
            _session("c1", "session-0001"),
            _session("c1", "session-0000", "ended", ended_at=NOW, ended_by="gm"),
            # A file-era id backfilled as ended may have no recorded times.
            CampaignSession(campaign_id="c1", session_id="Legacy: Night 1", status="ended",
                            origin="backfill"),
            _session("c2", "session-0001"),
        ])
    _refused(sqlite_database, _session("c1", "session-0002"),
             match=_unique("campaign_sessions", "campaign_id"))
    _refused(sqlite_database, _session("c1", "session-0000", "ended"),
             match=_unique("campaign_sessions", "campaign_id", "session_id"))
    _refused(sqlite_database, _session("c1", "session-0003", "ended_badly"),
             match=_check("ck_campaign_sessions_known_status"))
    # c3 has no sessions, so neither key can be the reason for these refusals.
    _refused(sqlite_database, _session("c3", "session-0004", "active", ended_at=NOW),
             match=_check("ck_campaign_sessions_active_not_ended"))
    _refused(sqlite_database, _session("c3", "session-0005", "active", ended_by="gm"),
             match=_check("ck_campaign_sessions_active_not_ended"))


def test_campaign_state_authority(sqlite_database):
    _seed(sqlite_database, "c1", "c2")
    with sqlite_database.transaction() as session:
        session.add(CampaignStateAuthority(campaign_id="c1", authority="file",
                                           stream_epoch=1, changed_at=NOW))
    _refused(sqlite_database, CampaignStateAuthority(campaign_id="c1", authority="db",
                                                     stream_epoch=2, changed_at=NOW),
             match=_unique("campaign_state_authority", "campaign_id"))
    _refused(sqlite_database, CampaignStateAuthority(campaign_id="c2", authority="shadow",
                                                     stream_epoch=1, changed_at=NOW),
             match=_check("ck_campaign_state_authority_known_authority"))
    _refused(sqlite_database, CampaignStateAuthority(campaign_id="c2", authority="db",
                                                     stream_epoch=0, changed_at=NOW),
             match=_check("ck_campaign_state_authority_stream_epoch_positive"))


# --------------------------------------------------------------------------
# characters.archived_at and the active-row locator index (section 7)


def test_locator_unique_only_among_active_rows(sqlite_database):
    _seed(sqlite_database, "c1")
    with sqlite_database.transaction() as session:
        session.add(_character("old-ana", "c1", legacy_file="Ana.json", archived_at=NOW))
        session.add(_character("new-ana", "c1", legacy_file="Ana.json"))
        session.add(_character("older-ana", "c1", legacy_file="Ana.json", archived_at=NOW))
    _refused(sqlite_database, _character("third-ana", "c1", legacy_file="Ana.json"),
             match=_unique("characters", "campaign_id", "legacy_storage", "legacy_file"))


# --------------------------------------------------------------------------
# migration_runs (section 7 and the PE-12 row of section 11)


def _run(source_checksum, *, campaign_id=None, kind="import", stream_epoch=None,
         status="running"):
    return MigrationRun(source_checksum=source_checksum, status=status, kind=kind,
                        campaign_id=campaign_id, stream_epoch=stream_epoch,
                        started_at=NOW)


def test_migration_run_uniqueness(sqlite_database):
    with sqlite_database.transaction() as session:
        session.add_all([
            _run("legacy"),
            _run("legacy", campaign_id="c1", stream_epoch=1),
            _run("legacy", campaign_id="c1", kind="export", stream_epoch=1),
            _run("abc", campaign_id="c1", stream_epoch=1, status="aborted"),
            _run("abc", campaign_id="c1", stream_epoch=1, status="aborted"),
            _run("abc", campaign_id="c1", stream_epoch=1),
            _run("abc", campaign_id="c2", stream_epoch=1),
        ])
    _refused(sqlite_database, _run("legacy"),
             match=_unique("migration_runs", "source_checksum"))
    _refused(sqlite_database, _run("abc", campaign_id="c1", stream_epoch=1,
                                   status="succeeded"),
             match=_unique("migration_runs", "campaign_id", "kind", "source_checksum",
                           "stream_epoch"))


@pytest.mark.parametrize("values, constraint", [
    ({"kind": "rollback", "campaign_id": "c1", "stream_epoch": 1}, "known_kind"),
    ({"status": "lost"}, "known_status"),
    ({"campaign_id": "c1"}, "scope"),
    ({"stream_epoch": 3}, "scope"),
    ({"kind": "export"}, "scope"),
    ({"campaign_id": "c1", "stream_epoch": 0}, "scope"),
    ({"status": "exported", "campaign_id": "c1", "stream_epoch": 1}, "exported_is_export"),
    ({"status": "aborted"}, "scope"),
])
def test_migration_run_rejects_invalid_shapes(sqlite_database, values, constraint):
    row = _run("x")
    for key, value in values.items():
        setattr(row, key, value)
    _refused(sqlite_database, row, match=_check("ck_migration_runs_" + constraint))


def test_legacy_repository_readers_ignore_campaign_runs(sqlite_database):
    with sqlite_database.transaction() as session:
        session.add(_run("same", campaign_id="c1", stream_epoch=1, status="succeeded"))
    with sqlite_database.transaction() as session:
        repository = MigrationRunRepository(session)
        assert repository.get_by_source_checksum("same") is None
        legacy = repository.start(source_checksum="same", source_path=None,
                                  tool_version="t")
        assert legacy.campaign_id is None and legacy.kind == "import"
        assert repository.start(source_checksum="same", source_path=None,
                                tool_version="t").id == legacy.id


def test_campaign_run_retry_after_abort_inserts_a_new_run(sqlite_database):
    with sqlite_database.transaction() as session:
        repository = MigrationRunRepository(session)
        first = repository.start_campaign_run(
            campaign_id="c1", kind="import", source_checksum="snap", stream_epoch=4,
            tool_version="t")
        assert first.status == "running" and first.stream_epoch == 4
        # The same attempt asked twice is the same row.
        assert repository.start_campaign_run(
            campaign_id="c1", kind="import", source_checksum="snap", stream_epoch=4,
            tool_version="t").id == first.id
        repository.finish(first, status="aborted")
        retry = repository.start_campaign_run(
            campaign_id="c1", kind="import", source_checksum="snap", stream_epoch=4,
            tool_version="t")
        assert retry.id != first.id
        repository.finish(retry, status="succeeded")
        export = repository.start_campaign_run(
            campaign_id="c1", kind="export", source_checksum="out", stream_epoch=6,
            tool_version="t", details={"manifest": {"files": 3}})
        repository.finish(export, status="exported")
        assert export.completed_at is not None
        assert repository.get_by_source_checksum("snap") is None


@pytest.mark.parametrize("kind, status", [
    ("import", "exported"), ("import", "failed"), ("import", "verified"),
    ("export", "succeeded"), ("import", "unknown"),
])
def test_campaign_run_finish_rejects_statuses_for_another_kind(sqlite_database, kind, status):
    with sqlite_database.transaction() as session:
        repository = MigrationRunRepository(session)
        run = repository.start_campaign_run(campaign_id="c1", kind=kind,
                                            source_checksum="s", stream_epoch=1,
                                            tool_version="t")
        with pytest.raises(ValueError):
            repository.finish(run, status=status)


def test_campaign_run_finishes_only_once(sqlite_database):
    with sqlite_database.transaction() as session:
        repository = MigrationRunRepository(session)
        run = repository.start_campaign_run(campaign_id="c1", kind="export",
                                            source_checksum="s", stream_epoch=2,
                                            tool_version="t")
        repository.finish(run, status="exported")
        completed_at = run.completed_at
        for status in ("exported", "aborted"):
            with pytest.raises(ValueError, match="already finished"):
                repository.finish(run, status=status)
        assert run.status == "exported" and run.completed_at == completed_at


def _unexported_campaigns(database):
    query = text(_load_migration()._STATE_ROWS_WITHOUT_NEWER_EXPORT)
    with database.session() as session:
        return session.execute(query).scalars().all()


def _check_stale_finish_is_refused(database, monkeypatch):
    """Codex PE-12 finding 1, replayed with two separately loaded objects.

    The export finishes at 10:00, state is written at 11:00, and an object
    loaded before the first finish tries to finish the same run at 12:00.
    That second finish must be refused by the stored row, and the downgrade's
    export check must still see c1's 11:00 state as unexported.
    """

    from core.persistence import repositories  # noqa: PLC0415

    at = {"now": datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(repositories, "utc_now", lambda: at["now"])
    _seed(database, "c1")
    with database.transaction() as session:
        run_id = MigrationRunRepository(session).start_campaign_run(
            campaign_id="c1", kind="export", source_checksum="out", stream_epoch=1,
            tool_version="t").id

    with database.session() as stale_session:
        # Loaded while the run is still running, and kept: sessions do not
        # expire on commit, so nothing refreshes it behind the caller's back.
        stale = stale_session.get(MigrationRun, run_id)
        assert stale.status == "running"
        stale_session.commit()

        with database.transaction() as session:
            run = session.get(MigrationRun, run_id)
            MigrationRunRepository(session).finish(
                run, status="exported", entity_counts={"files": 3},
                details={"manifest": "first"})
        assert stale.status == "running"

        with database.transaction() as session:
            later = _state("c1", "pc-c1", "play")
            later.updated_at = datetime(2026, 11, 10, 11, 0, tzinfo=timezone.utc)
            session.add(later)
        assert _unexported_campaigns(database) == ["c1"]

        at["now"] = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
        for status in ("exported", "aborted"):
            with pytest.raises(ValueError, match="already finished"):
                MigrationRunRepository(stale_session).finish(
                    stale, status=status, entity_counts={"files": 99},
                    details={"manifest": "second"})
            stale_session.commit()
        # Nothing the refused finish carried is left pending on the object.
        assert not stale_session.dirty

    with database.session() as session:
        stored = session.get(MigrationRun, run_id)
        assert stored.status == "exported"
        completed_at = stored.completed_at
        if completed_at.tzinfo is None:  # SQLite hands back naive UTC
            completed_at = completed_at.replace(tzinfo=timezone.utc)
        assert completed_at == datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
        assert stored.entity_counts == {"files": 3}
        assert stored.details == {"manifest": "first"}
    assert _unexported_campaigns(database) == ["c1"]


def test_stale_campaign_run_object_cannot_finish_again(sqlite_url, monkeypatch):
    database = Database(sqlite_url)
    try:
        database.create_schema()
        _check_stale_finish_is_refused(database, monkeypatch)
    finally:
        database.dispose()


def test_campaign_finish_rolled_back_in_a_savepoint_can_finish_again(sqlite_database):
    with sqlite_database.transaction() as session:
        repository = MigrationRunRepository(session)
        run = repository.start_campaign_run(campaign_id="c1", kind="export",
                                            source_checksum="s", stream_epoch=1,
                                            tool_version="t")
        savepoint = session.begin_nested()
        repository.finish(run, status="exported")
        savepoint.rollback()
        assert run.status == "running" and run.completed_at is None
        repository.finish(run, status="aborted")
    with sqlite_database.session() as session:
        assert session.get(MigrationRun, run.id).status == "aborted"


def _run_fields(run):
    return (run.status, run.completed_at, run.entity_counts, run.details)


def _check_finish_read_then_rolled_back(database):
    """Codex PE-12 R2-1: the finish result is read before the SAVEPOINT rolls back.

    Reading the returned fields must not leave the object, the stored row and
    a rejoined attempt disagreeing once the finish is undone; afterwards the
    run must still finish normally.
    """

    attempt = {"campaign_id": "c1", "kind": "export", "source_checksum": "s",
               "stream_epoch": 1, "tool_version": "t"}
    stored_row = text("SELECT status, completed_at FROM migration_runs WHERE id = :id")
    with database.transaction() as session:
        repository = MigrationRunRepository(session)
        run = repository.start_campaign_run(**attempt)
        savepoint = session.begin_nested()
        returned = repository.finish(run, status="exported",
                                     entity_counts={"characters": 2},
                                     details={"manifest": "rolled-back"})
        status, completed_at, counts, details = _run_fields(returned)
        assert (status, counts, details) == (
            "exported", {"characters": 2}, {"manifest": "rolled-back"})
        assert completed_at is not None
        savepoint.rollback()

        assert _run_fields(run) == ("running", None, {}, {})
        assert tuple(session.execute(stored_row, {"id": run.id}).one()) == (
            "running", None)
        rejoined = repository.start_campaign_run(**attempt)
        assert rejoined is run
        assert _run_fields(rejoined) == ("running", None, {}, {})

        repository.finish(run, status="exported", entity_counts={"characters": 3},
                          details={"manifest": "kept"})
        assert run.status == "exported"
        assert run.entity_counts == {"characters": 3}
    with database.session() as session:
        stored = session.get(MigrationRun, run.id)
        assert (stored.status, stored.entity_counts, stored.details) == (
            "exported", {"characters": 3}, {"manifest": "kept"})
        assert stored.completed_at is not None


def test_campaign_finish_read_then_rolled_back_in_a_savepoint(sqlite_database):
    _check_finish_read_then_rolled_back(sqlite_database)


def test_campaign_finish_records_the_stored_values_on_the_caller(sqlite_database):
    with sqlite_database.transaction() as session:
        repository = MigrationRunRepository(session)
        run = repository.start_campaign_run(campaign_id="c1", kind="import",
                                            source_checksum="s", stream_epoch=1,
                                            tool_version="t")
        repository.finish(run, status="succeeded", entity_counts={"characters": 2},
                          details={"note": "ok"})
        assert run.status == "succeeded" and run.completed_at is not None
        assert not session.dirty
    with sqlite_database.session() as session:
        stored = session.get(MigrationRun, run.id)
        assert (stored.status, stored.entity_counts, stored.details) == (
            "succeeded", {"characters": 2}, {"note": "ok"})


def test_legacy_run_still_moves_from_succeeded_to_verified(sqlite_database):
    with sqlite_database.transaction() as session:
        repository = MigrationRunRepository(session)
        run = repository.start(source_checksum="s", source_path=None, tool_version="t")
        repository.finish(run, status="succeeded")
    with sqlite_database.transaction() as session:
        # A separately loaded object, as the import tool's verify step uses.
        run = session.get(MigrationRun, run.id)
        MigrationRunRepository(session).finish(run, status="verified")
    with sqlite_database.session() as session:
        assert session.get(MigrationRun, run.id).status == "verified"


@pytest.mark.parametrize("status", ["exported", "aborted"])
def test_legacy_run_finish_rejects_campaign_statuses(sqlite_database, status):
    with sqlite_database.transaction() as session:
        repository = MigrationRunRepository(session)
        run = repository.start(source_checksum="s", source_path=None, tool_version="t")
        with pytest.raises(ValueError):
            repository.finish(run, status=status)


@pytest.mark.parametrize("bad", [
    {"campaign_id": ""}, {"kind": "rollback"}, {"stream_epoch": 0},
])
def test_campaign_run_start_validates_inputs(sqlite_database, bad):
    values = {"campaign_id": "c1", "kind": "import", "source_checksum": "s",
              "stream_epoch": 1, "tool_version": "t"}
    values.update(bad)
    with sqlite_database.transaction() as session:
        with pytest.raises(ValueError):
            MigrationRunRepository(session).start_campaign_run(**values)


# --------------------------------------------------------------------------
# Alembic upgrade/downgrade on SQLite


def _alembic(url, *args, check=True):
    environment = os.environ.copy()
    environment["DATABASE_URL"] = url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=REPO_ROOT, env=environment,
        check=check, capture_output=True, text=True, encoding="utf-8",
    )


@pytest.fixture
def sqlite_url(tmp_path):
    return "sqlite+pysqlite:///" + (tmp_path / "migrated.sqlite3").as_posix()


def _seed_previous_schema(engine, *, false="0"):
    """Insert one of every pre-PE-12 row kind with raw SQL at revision 0002."""
    statements = [
        "INSERT INTO users (id, username, normalized_username, display_name, password_hash,"
        " is_admin, session_version, created_at, source_payload)"
        " VALUES ('gm', 'gm', 'gm', 'GM', 'h', {false}, 0, '2026-10-01 00:00:00', '{}')",
        "INSERT INTO users (id, username, normalized_username, display_name, password_hash,"
        " is_admin, session_version, created_at, source_payload)"
        " VALUES ('pl', 'pl', 'pl', 'PL', 'h', {false}, 0, '2026-10-01 00:00:00', '{}')",
        "INSERT INTO campaigns (id, slug, name, system, created_by_user_id, created_at,"
        " session_number, settings, source_payload, source_checksum)"
        " VALUES ('c1', 's', 'C', 'pf2e', 'gm', '2026-10-01 00:00:00', 1, '{}', '{}', 'cs')",
        "INSERT INTO characters (id, campaign_id, system, display_name, legacy_storage,"
        " legacy_file, content_checksum, created_at, updated_at)"
        " VALUES ('pc1', 'c1', 'pf2e', 'Ana', 'party_data', 'Ana.json', 'x',"
        " '2026-10-01 00:00:00', '2026-10-01 00:00:00')",
        "INSERT INTO campaign_memberships (campaign_id, user_id, role, character_id,"
        " joined_at, updated_at) VALUES ('c1', 'gm', 'gm', NULL, '2026-10-01 00:00:00',"
        " '2026-10-01 00:00:00')",
        "INSERT INTO campaign_memberships (campaign_id, user_id, role, character_id,"
        " joined_at, updated_at) VALUES ('c1', 'pl', 'player', 'pc1', '2026-10-01 00:00:00',"
        " '2026-10-01 00:00:00')",
        "INSERT INTO character_assignments (campaign_id, character_id, user_id, role,"
        " created_at, updated_at) VALUES ('c1', 'pc1', 'pl', 'owner', '2026-10-01 00:00:00',"
        " '2026-10-01 00:00:00')",
        "INSERT INTO character_drafts (id, campaign_id, user_id, character_id, kind, state,"
        " revision, payload, created_at, updated_at) VALUES ('d1', 'c1', 'pl', 'pc1',"
        " 'level_up', 'active', 2, '{}', '2026-10-01 00:00:00', '2026-10-01 00:00:00')",
        "INSERT INTO character_workflow_receipts (id, campaign_id, author_id, operation,"
        " key_hash, request_digest, state, details, created_at, updated_at) VALUES"
        " ('r1', 'c1', 'pl', 'create_draft', 'k', 'd', 'committed', '{}',"
        " '2026-10-01 00:00:00', '2026-10-01 00:00:00')",
        "INSERT INTO migration_runs (id, source_checksum, status, entity_counts, details,"
        " started_at, completed_at) VALUES ('m1', 'legacy-snapshot', 'verified', '{}',"
        " '{}', '2026-10-01 00:00:00', '2026-10-01 00:00:01')",
    ]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement.replace("{false}", false)))


_PRESERVED = {
    "users": 2, "campaigns": 1, "characters": 1, "campaign_memberships": 2,
    "character_assignments": 1, "character_drafts": 1, "character_workflow_receipts": 1,
    "migration_runs": 1,
}


def _counts(connection):
    return {table: connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            for table in _PRESERVED}


def test_sqlite_upgrade_preserves_rows_and_constraints(sqlite_url):
    _alembic(sqlite_url, "upgrade", PREVIOUS)
    engine = create_engine(sqlite_url)
    try:
        _seed_previous_schema(engine)
        _alembic(sqlite_url, "upgrade", "head")
        with engine.connect() as connection:
            assert _counts(connection) == _PRESERVED
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
            run = connection.execute(text(
                "SELECT campaign_id, stream_epoch, kind, status FROM migration_runs")).one()
            assert tuple(run) == (None, None, "import", "verified")
            assert connection.execute(text(
                "SELECT archived_at FROM characters")).scalar() is None
            assert connection.execute(text(
                "SELECT version_num FROM alembic_version")).scalar() == HEAD
        _assert_head_constraints(engine)
    finally:
        engine.dispose()


def _assert_head_constraints(engine):
    """The rebuilt tables keep every check and the new partial indexes work."""
    statements_that_must_fail = [
        # Existing checks on the rebuilt `characters` table.
        "INSERT INTO characters (id, campaign_id, system, legacy_storage, legacy_file,"
        " content_checksum, created_at, updated_at) VALUES ('bad1', 'c1', 'pf2e',"
        " 'elsewhere', 'b.json', 'x', '2026-10-01', '2026-10-01')",
        "INSERT INTO characters (id, campaign_id, system, legacy_storage, legacy_file,"
        " content_checksum, created_at, updated_at) VALUES ('bad2', 'c1', 'nope',"
        " 'party_data', 'b.json', 'x', '2026-10-01', '2026-10-01')",
        # Active-row locator uniqueness.
        "INSERT INTO characters (id, campaign_id, system, legacy_storage, legacy_file,"
        " content_checksum, created_at, updated_at) VALUES ('dup', 'c1', 'pf2e',"
        " 'party_data', 'Ana.json', 'x', '2026-10-01', '2026-10-01')",
        # The (campaign_id, id) key other tables reference.
        "INSERT INTO characters (id, campaign_id, system, legacy_storage, legacy_file,"
        " content_checksum, created_at, updated_at) VALUES ('pc1', 'c1', 'pf2e',"
        " 'party_data', 'Zed.json', 'x', '2026-10-01', '2026-10-01')",
        # Legacy run checksum uniqueness survives.
        "INSERT INTO migration_runs (id, source_checksum, status, kind, entity_counts,"
        " details, started_at) VALUES ('m2', 'legacy-snapshot', 'running', 'import', '{}',"
        " '{}', '2026-10-01')",
        # migration_runs checks.
        "INSERT INTO migration_runs (id, source_checksum, status, kind, entity_counts,"
        " details, started_at) VALUES ('m3', 'z', 'lost', 'import', '{}', '{}',"
        " '2026-10-01')",
    ]
    for statement in statements_that_must_fail:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            connection.execute(text(statement))
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE characters SET archived_at = '2026-11-01' WHERE id = 'pc1'"))
        connection.execute(text(
            "INSERT INTO characters (id, campaign_id, system, legacy_storage, legacy_file,"
            " content_checksum, created_at, updated_at) VALUES ('pc2', 'c1', 'pf2e',"
            " 'party_data', 'Ana.json', 'x', '2026-10-01', '2026-10-01')"))
    names = {index["name"] for index in inspect(engine).get_indexes("characters")}
    assert "uq_characters_active_legacy_locator" in names
    assert "ix_characters_campaign_id" in names


def test_sqlite_empty_downgrade_restores_previous_schema(sqlite_url):
    _alembic(sqlite_url, "upgrade", "head")
    _alembic(sqlite_url, "downgrade", PREVIOUS)
    engine = create_engine(sqlite_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert not tables & {"character_states", "encounter_states", "encounter_combatants",
                             "campaign_clocks", "campaign_sessions",
                             "campaign_state_authority"}
        assert "archived_at" not in {c["name"] for c in inspector.get_columns("characters")}
        assert "kind" not in {c["name"] for c in inspector.get_columns("migration_runs")}
        uniques = {u["name"] for u in inspector.get_unique_constraints("characters")}
        assert "uq_characters_legacy_locator" in uniques
        uniques = {u["name"] for u in inspector.get_unique_constraints("migration_runs")}
        assert "uq_migration_runs_source_checksum" in uniques
    finally:
        engine.dispose()
    # And the restored schema upgrades again cleanly.
    _alembic(sqlite_url, "upgrade", "head")


def _upgraded_with_rows(url):
    _alembic(url, "upgrade", PREVIOUS)
    engine = create_engine(url)
    _seed_previous_schema(engine)
    _alembic(url, "upgrade", "head")
    return engine


def _insert(connection, statement, **params):
    connection.execute(text(statement), params)


def _add_state(connection, updated_at):
    _insert(connection,
            "INSERT INTO character_states (campaign_id, character_id, domain, schema_version,"
            " revision, rules_profile, content, payload, created_at, updated_at) VALUES"
            " ('c1', 'pc1', 'play', 1, 3, 'pf2e', '{}', '{}', :at, :at)", at=updated_at)


def _add_export(connection, run_id, status, completed_at, *, checksum="out", epoch=2):
    _insert(connection,
            "INSERT INTO migration_runs (id, source_checksum, status, kind, campaign_id,"
            " stream_epoch, entity_counts, details, started_at, completed_at) VALUES"
            " (:id, :checksum, :status, 'export', 'c1', :epoch, '{}', '{}', :at, :done)",
            id=run_id, checksum=checksum, status=status, epoch=epoch,
            at="2026-11-01 00:00:00", done=completed_at)


def _downgrade_refused(url, message):
    completed = _alembic(url, "downgrade", PREVIOUS, check=False)
    assert completed.returncode != 0
    # Match the raised error only: Alembic's own "Running downgrade ..." log
    # line also lands on stderr and contains words like "authority".
    errors = [line for line in completed.stderr.splitlines()
              if line.startswith("RuntimeError: ")]
    assert errors and message in errors[-1], completed.stderr
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT version_num FROM alembic_version")).scalar() == HEAD
    finally:
        engine.dispose()


@pytest.mark.parametrize("authority", ["db", "migrating", "exporting"])
def test_downgrade_refused_while_campaign_is_not_file_authority(sqlite_url, authority):
    engine = _upgraded_with_rows(sqlite_url)
    with engine.begin() as connection:
        _insert(connection,
                "INSERT INTO campaign_state_authority (campaign_id, authority, stream_epoch,"
                " changed_at) VALUES ('c1', :authority, 2, '2026-11-01')", authority=authority)
    engine.dispose()
    _downgrade_refused(sqlite_url, "campaign state authority is not file for c1 (" + authority)


@pytest.mark.parametrize("statement, refusal", [
    ("INSERT INTO campaign_sessions (campaign_id, session_id, status, origin)"
     " VALUES ('c1', 'old', 'ended', 'backfill')", "session rows exist"),
    ("INSERT INTO campaign_clocks (campaign_id, clock, value, revision, updated_at)"
     " VALUES ('c1', 'world', 0, 0, '2026-11-01')", "campaign clock rows exist"),
    ("UPDATE characters SET archived_at = '2026-11-01' WHERE id = 'pc1'",
     "archived characters exist"),
])
def test_downgrade_refused_for_rows_it_cannot_represent(sqlite_url, statement, refusal):
    engine = _upgraded_with_rows(sqlite_url)
    with engine.begin() as connection:
        _insert(connection, statement)
    engine.dispose()
    _downgrade_refused(sqlite_url, refusal)


def test_downgrade_refused_without_a_newer_verified_export(sqlite_url):
    engine = _upgraded_with_rows(sqlite_url)
    with engine.begin() as connection:
        _add_export(connection, "x1", "exported", "2026-11-02 00:00:00", epoch=2)
        _add_state(connection, "2026-11-03 00:00:00")
        # A later export that never finished does not count.
        _add_export(connection, "x2", "running", None, checksum="out2", epoch=3)
    engine.dispose()
    _downgrade_refused(sqlite_url, "state rows in c1 have no newer verified export")


def test_downgrade_refused_with_state_and_no_export(sqlite_url):
    engine = _upgraded_with_rows(sqlite_url)
    with engine.begin() as connection:
        _add_state(connection, "2026-11-03 00:00:00")
    engine.dispose()
    _downgrade_refused(sqlite_url, "state rows in c1 have no newer verified export")


def test_downgrade_after_verified_export_drops_state_and_campaign_runs(sqlite_url):
    engine = _upgraded_with_rows(sqlite_url)
    with engine.begin() as connection:
        _add_state(connection, "2026-11-03 00:00:00")
        _insert(connection,
                "INSERT INTO campaign_state_authority (campaign_id, authority, stream_epoch,"
                " changed_at) VALUES ('c1', 'file', 5, '2026-11-04')")
        # An aborted import and its retry share a checksum (different epochs).
        for run_id, status, epoch in (("i1", "aborted", 1), ("i2", "succeeded", 2)):
            _insert(connection,
                    "INSERT INTO migration_runs (id, source_checksum, status, kind,"
                    " campaign_id, stream_epoch, entity_counts, details, started_at)"
                    " VALUES (:id, 'legacy-snapshot', :status, 'import', 'c1', :epoch,"
                    " '{}', '{}', '2026-11-01')", id=run_id, status=status, epoch=epoch)
        _add_export(connection, "x1", "exported", "2026-11-04 00:00:00", epoch=4)
    engine.dispose()
    _alembic(sqlite_url, "downgrade", PREVIOUS)
    engine = create_engine(sqlite_url)
    try:
        with engine.connect() as connection:
            assert _counts(connection) == _PRESERVED
            assert connection.execute(text(
                "SELECT id FROM migration_runs")).scalars().all() == ["m1"]
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    finally:
        engine.dispose()


_STATE_TABLES = {"character_states", "encounter_states", "encounter_combatants",
                 "campaign_clocks", "campaign_sessions", "campaign_state_authority"}


def _schema_snapshot(url):
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        with engine.connect() as connection:
            version = connection.execute(text(
                "SELECT version_num FROM alembic_version")).scalar()
        return {
            "version": version,
            "tables": set(inspector.get_table_names()),
            "characters": {c["name"] for c in inspector.get_columns("characters")},
            "migration_runs": {c["name"] for c in inspector.get_columns("migration_runs")},
        }
    finally:
        engine.dispose()


def _dangle_a_membership(url):
    engine = create_engine(url)
    try:
        with engine.begin() as connection:  # foreign keys are off on this engine
            connection.execute(text(
                "INSERT INTO campaign_memberships (campaign_id, user_id, role, joined_at,"
                " updated_at) VALUES ('c1', 'ghost', 'player', '2026-10-01', '2026-10-01')"))
    finally:
        engine.dispose()


def test_sqlite_upgrade_refuses_existing_foreign_key_violations_before_any_change(sqlite_url):
    _alembic(sqlite_url, "upgrade", PREVIOUS)
    engine = create_engine(sqlite_url)
    _seed_previous_schema(engine)
    engine.dispose()
    _dangle_a_membership(sqlite_url)
    before = _schema_snapshot(sqlite_url)
    completed = _alembic(sqlite_url, "upgrade", "head", check=False)
    assert completed.returncode != 0
    assert "before the revision changed anything" in completed.stderr
    assert _schema_snapshot(sqlite_url) == before


def test_failed_sqlite_upgrade_rolls_back_every_step(sqlite_url):
    """A failure after both table rebuilds leaves the 0002 file untouched."""
    _alembic(sqlite_url, "upgrade", PREVIOUS)
    engine = create_engine(sqlite_url)
    _seed_previous_schema(engine)
    with engine.begin() as connection:
        # Makes the last step (creating the state tables) fail.
        connection.execute(text("CREATE TABLE campaign_clocks (x INTEGER)"))
    engine.dispose()
    before = _schema_snapshot(sqlite_url)
    assert _alembic(sqlite_url, "upgrade", "head", check=False).returncode != 0
    after = _schema_snapshot(sqlite_url)
    assert after == before
    assert not any(name.startswith("_alembic_tmp") for name in after["tables"])
    engine = create_engine(sqlite_url)
    try:
        with engine.connect() as connection:
            assert _counts(connection) == _PRESERVED
    finally:
        engine.dispose()


def test_failed_sqlite_downgrade_rolls_back_every_step(sqlite_url):
    """A failure after the state tables are dropped restores them."""
    engine = _upgraded_with_rows(sqlite_url)
    with engine.begin() as connection:
        _add_state(connection, "2026-11-03 00:00:00")
        _add_export(connection, "x1", "exported", "2026-11-04 00:00:00")
        # Makes the last step (rebuilding `characters`) fail.
        connection.execute(text("CREATE TABLE _alembic_tmp_characters (x INTEGER)"))
    engine.dispose()
    before = _schema_snapshot(sqlite_url)
    assert _alembic(sqlite_url, "downgrade", PREVIOUS, check=False).returncode != 0
    after = _schema_snapshot(sqlite_url)
    assert after == before and _STATE_TABLES <= after["tables"]
    engine = create_engine(sqlite_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT COUNT(*) FROM character_states")).scalar() == 1
            assert connection.execute(text(
                "SELECT COUNT(*) FROM migration_runs WHERE campaign_id IS NOT NULL")).scalar() == 1
    finally:
        engine.dispose()


def test_sqlite_downgrade_refuses_existing_foreign_key_violations(sqlite_url):
    engine = _upgraded_with_rows(sqlite_url)
    engine.dispose()
    _dangle_a_membership(sqlite_url)
    before = _schema_snapshot(sqlite_url)
    completed = _alembic(sqlite_url, "downgrade", PREVIOUS, check=False)
    assert completed.returncode != 0
    assert "before the revision changed anything" in completed.stderr
    assert _schema_snapshot(sqlite_url) == before


def test_offline_downgrade_is_refused(sqlite_url):
    completed = _alembic(sqlite_url, "downgrade", f"{HEAD}:{PREVIOUS}", "--sql",
                         check=False)
    assert completed.returncode != 0
    assert "online" in completed.stderr


def test_sqlite_upgrade_refuses_when_foreign_keys_are_enforced(tmp_path, monkeypatch):
    """The `characters` rebuild must never run with cascading foreign keys on."""
    migration = _load_migration()
    engine = create_engine("sqlite+pysqlite:///" + (tmp_path / "fk.sqlite3").as_posix())
    try:
        with engine.connect() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            with pytest.raises(RuntimeError, match="foreign keys"):
                migration._require_sqlite_foreign_keys_off(connection)
            connection.execute(text("PRAGMA foreign_keys=OFF"))
            migration._require_sqlite_foreign_keys_off(connection)
    finally:
        engine.dispose()


def _load_migration():
    import importlib.util  # noqa: PLC0415

    path = REPO_ROOT / "migrations" / "versions" / "20261110_0003_game_state_schema.py"
    spec = importlib.util.spec_from_file_location("pe12_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_readiness_pin_is_the_single_alembic_head():
    completed = _alembic("sqlite+pysqlite:///:memory:", "heads")
    heads = [line.split()[0] for line in completed.stdout.splitlines() if line.strip()]
    assert heads == [HEAD]


# --------------------------------------------------------------------------
# PostgreSQL


def _postgresql_url():
    raw_url = os.environ.get("DATABASE_URL")
    assert raw_url, "DATABASE_URL is required for PostgreSQL schema tests"
    url = normalize_database_url(raw_url)
    assert make_url(url).get_backend_name() == "postgresql"
    return url


@pytest.fixture
def postgres_schema():
    url = _postgresql_url()
    schema = "gm_pf2e_pe12_" + uuid.uuid4().hex
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        yield url, schema
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def postgres_database(postgres_schema):
    url, schema = postgres_schema
    database = Database(url, engine_options={
        "connect_args": {"options": f"-csearch_path={schema}"}})
    try:
        database.create_schema()
        yield database
    finally:
        database.dispose()


def _schema_url(url, schema):
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}options=-csearch_path%3D{schema}"


@pytest.mark.postgresql
def test_postgresql_partial_indexes_and_composite_keys(postgres_database):
    _seed(postgres_database, "c1", "c2")
    with postgres_database.transaction() as session:
        session.add_all([_encounter("c1", "e1"), _encounter("c1", "e0", status="ended"),
                         _session("c1", "s1"),
                         _session("c1", "s0", "ended", ended_at=NOW, ended_by="gm"),
                         _state("c1", "pc-c1", "play")])
    _refused(postgres_database, _encounter("c1", "e2"),
             match=_pg("uq_encounter_states_one_active"))
    _refused(postgres_database, _session("c1", "s2"),
             match=_pg("uq_campaign_sessions_one_active"))
    _refused(postgres_database, _state("c1", "pc-c2", "build"),
             match=_pg("fk_character_states_character_campaign"))
    with postgres_database.transaction() as session:
        session.add(_character("old", "c1", legacy_file="Ana.json", archived_at=NOW))
        session.add(_character("new", "c1", legacy_file="Ana.json"))
    _refused(postgres_database, _character("dup", "c1", legacy_file="Ana.json"),
             match=_pg("uq_characters_active_legacy_locator"))
    with postgres_database.transaction() as session:
        session.add_all([_run("snap"), _run("snap", campaign_id="c1", stream_epoch=1,
                                            status="aborted"),
                         _run("snap", campaign_id="c1", stream_epoch=1)])
    _refused(postgres_database, _run("snap"),
             match=_pg("uq_migration_runs_legacy_source_checksum"))
    _refused(postgres_database, _run("snap", campaign_id="c1", stream_epoch=1),
             match=_pg("uq_migration_runs_campaign_attempt"))


@pytest.mark.postgresql
def test_postgresql_campaign_finish_read_then_rolled_back(postgres_database):
    _check_finish_read_then_rolled_back(postgres_database)


@pytest.mark.postgresql
def test_postgresql_stale_campaign_run_object_cannot_finish_again(postgres_database,
                                                                  monkeypatch):
    _check_stale_finish_is_refused(postgres_database, monkeypatch)


@pytest.mark.postgresql
def test_postgresql_upgrade_downgrade_round_trip(postgres_schema):
    url, schema = postgres_schema
    migrated_url = _schema_url(url, schema)
    _alembic(migrated_url, "upgrade", PREVIOUS)
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        _seed_previous_schema(engine, false="false")
        _alembic(migrated_url, "upgrade", "head")
        with engine.connect() as connection:
            assert _counts(connection) == _PRESERVED
            assert connection.execute(text(
                "SELECT kind FROM migration_runs")).scalar() == "import"
        with engine.begin() as connection:
            _add_state(connection, "2026-11-03 00:00:00")
            _add_export(connection, "x1", "exported", "2026-11-02 00:00:00")
        completed = _alembic(migrated_url, "downgrade", PREVIOUS, check=False)
        assert completed.returncode != 0
        assert "RuntimeError: downgrade refused: state rows in c1 have no newer" in completed.stderr
        with engine.begin() as connection:
            _add_export(connection, "x2", "exported", "2026-11-05 00:00:00",
                        checksum="out2", epoch=3)
        _alembic(migrated_url, "downgrade", PREVIOUS)
        with engine.connect() as connection:
            assert _counts(connection) == _PRESERVED
            assert connection.execute(text(
                "SELECT version_num FROM alembic_version")).scalar() == PREVIOUS
    finally:
        engine.dispose()
