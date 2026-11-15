"""PE-13a: campaign-scoped state repositories and the command lock order."""

from __future__ import annotations

import dataclasses
import inspect
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from core.persistence import (
    AuthorityMismatch,
    Campaign,
    CampaignClock,
    CampaignClockRepository,
    CampaignPinRepository,
    CampaignStateAuthority,
    Character,
    CharacterState,
    CharacterStateRepository,
    CommandLocks,
    EncounterState,
    EncounterStateRepository,
    LockOrderError,
    PinUnavailable,
    ProfileMismatch,
    RevisionConflict,
    StateAuthorityRepository,
    StateRecord,
    StateRowMissing,
    UnknownSchemaVersion,
    User,
)
from core.persistence import command_locks, state_repositories

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
CONTENT = {"ruleset_id": "pf2e-legacy-unknown", "manifest_sha256": None}
PIN = {"profile": "pf2e", "content": CONTENT}


@pytest.fixture(autouse=True)
def _ticking_clock(monkeypatch):
    """Make ``utc_now`` strictly increasing so ``updated_at`` moves are visible."""

    ticks = iter(range(1, 10_000))
    monkeypatch.setattr(
        state_repositories, "utc_now", lambda: NOW + timedelta(seconds=next(ticks))
    )


def _seed(database, authority=None, *, pins=True):
    """Campaigns A and B with characters a1, a2 (A) and b1 (B), PF2e pins."""

    authority = {"A": "db", "B": "db"} if authority is None else authority
    with database.transaction() as session:
        session.add(User(id="gm", username="gm", normalized_username="gm",
                         display_name="gm", password_hash="h", created_at=NOW))
        session.flush()
        for cid in ("A", "B"):
            session.add(Campaign(id=cid, slug=cid, name=cid, system="pf2e",
                                 created_by_user_id="gm", created_at=NOW,
                                 settings={"rules": PIN} if pins else {}))
        session.flush()
        for character_id, cid in (("a1", "A"), ("a2", "A"), ("b1", "B")):
            session.add(Character(id=character_id, campaign_id=cid, system="pf2e",
                                  display_name=character_id, legacy_storage="party_data",
                                  legacy_file=character_id + ".json",
                                  content_checksum="0" * 64, created_at=NOW,
                                  updated_at=NOW))
        for cid, value in authority.items():
            if value is not None:
                session.add(CampaignStateAuthority(campaign_id=cid, authority=value,
                                                   stream_epoch=1, changed_at=NOW))


def _set_authority(database, campaign_id, value):
    with database.transaction() as session:
        session.execute(update(CampaignStateAuthority)
                        .where(CampaignStateAuthority.campaign_id == campaign_id)
                        .values(authority=value))


def _states(session):
    return CharacterStateRepository(session)


def _insert_state(session, cid, character_id, domain, payload=None):
    return _states(session).insert(cid, character_id, domain, rules_profile="pf2e",
                                   content=CONTENT, payload=payload or {"hp": 10})


def _insert_encounter(session, cid, eid, **fields):
    return EncounterStateRepository(session).insert(
        cid, eid, rules_profile="pf2e", content=CONTENT, **fields)


def _stored_state(database, cid, character_id, domain):
    with database.session() as session:
        return session.execute(select(CharacterState.__table__).where(
            CharacterState.campaign_id == cid,
            CharacterState.character_id == character_id,
            CharacterState.domain == domain)).first()


def _stub_stream(calls):
    def lock_stream(session, campaign_id):
        calls.append((session, campaign_id))
        return {"campaign_id": campaign_id}
    return lock_stream


# --------------------------------------------------------------------------
# revisions (Decisions 1 and 2)


def test_insert_starts_at_one_and_each_write_bumps_by_one(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        record = _insert_state(session, "A", "a1", "play")
    assert record.revision == 1 and record.kind == "character.play" and record.id == "a1"
    first = _stored_state(sqlite_database, "A", "a1", "play")
    with sqlite_database.transaction() as session:
        record = _states(session).write("A", "a1", "play", read_revision=1,
                                        payload={"hp": 9})
        record = _states(session).write("A", "a1", "play", read_revision=2,
                                        payload={"hp": 9})  # unchanged payload still bumps
    second = _stored_state(sqlite_database, "A", "a1", "play")
    assert record.revision == 3 and second.revision == 3
    assert second.payload == {"hp": 9}
    assert second.updated_at > first.updated_at


def test_stale_compare_and_set_raises_and_writes_nothing(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        _insert_state(session, "A", "a1", "play")
        _states(session).write("A", "a1", "play", read_revision=1, payload={"hp": 8})
    before = _stored_state(sqlite_database, "A", "a1", "play")
    for stale in (1, 0):  # another write in between; and a lower :read
        with sqlite_database.transaction() as session:
            with pytest.raises(RevisionConflict):
                _states(session).write("A", "a1", "play", read_revision=stale,
                                       payload={"hp": 1})
    after = _stored_state(sqlite_database, "A", "a1", "play")
    assert after.revision == before.revision == 2 and after.payload == {"hp": 8}
    assert after.updated_at == before.updated_at


def test_no_public_method_can_set_a_revision():
    classes = (state_repositories.CharacterStateRepository,
               state_repositories.EncounterStateRepository,
               state_repositories.CampaignClockRepository,
               command_locks.CommandLocks)
    for cls in classes:
        for name, member in inspect.getmembers(cls, inspect.isfunction):
            if name.startswith("_"):
                continue
            params = inspect.signature(member).parameters
            assert "revision" not in params, f"{cls.__name__}.{name}"


def test_keyword_writers_refuse_revision_fields(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        repo = EncounterStateRepository(session)
        _insert_encounter(session, "A", "e1")
        repo.add_combatant("A", "e1", read_revision=1, combatant_id="k1", kind="creature")
        for field in ("revision", "schema_version", "updated_at"):
            with pytest.raises(ValueError):
                repo.write("A", "e1", read_revision=2, **{field: 1})
            with pytest.raises(ValueError):
                repo.update_combatant("A", "e1", read_revision=2, combatant_id="k1",
                                      **{field: 1})
        assert repo.get("A", "e1").revision == 2


def test_build_and_play_revisions_are_independent(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        _insert_state(session, "A", "a1", "build", {"name": "x"})
        _insert_state(session, "A", "a1", "play")
        _states(session).write("A", "a1", "play", read_revision=1, payload={"hp": 3})
        _states(session).write("A", "a1", "play", read_revision=2, payload={"hp": 2})
        build = _states(session).get("A", "a1", "build")
        _states(session).write("A", "a1", "build", read_revision=1, payload={"name": "y"})
        play = _states(session).get("A", "a1", "play")
    assert build.revision == 1 and play.revision == 3


def test_combatant_changes_bump_the_encounter_once_and_pc_vitals_do_not(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        repo = EncounterStateRepository(session)
        assert _insert_encounter(session, "A", "e1").revision == 1
        _insert_state(session, "A", "a1", "play")
        record = repo.add_combatant("A", "e1", read_revision=1, combatant_id="k1",
                                    kind="pc", character_id="a1")
        assert record.revision == 2
        record = repo.update_combatant("A", "e1", read_revision=2, combatant_id="k1",
                                       legacy_ref="instance-1")
        assert record.revision == 3
        _states(session).write("A", "a1", "play", read_revision=1, payload={"hp": 1})
        assert repo.get("A", "e1").revision == 3
        record = repo.remove_combatant("A", "e1", read_revision=3, combatant_id="k1")
        assert record.revision == 4 and repo.combatants("A", "e1") == []
        with pytest.raises(RevisionConflict):
            repo.add_combatant("A", "e1", read_revision=3, combatant_id="k2",
                               kind="creature", creature_ref="bestiary/goblin")
        assert repo.combatants("A", "e1") == []


# --------------------------------------------------------------------------
# campaign scope, campaign-wide reads


def test_every_lookup_is_campaign_scoped(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        _insert_state(session, "A", "a1", "play")
        _insert_encounter(session, "A", "e1")
        CampaignClockRepository(session).insert("A", "world", value=5)
        CampaignClockRepository(session).insert("B", "world", value=9)
    with sqlite_database.transaction() as session:
        assert _states(session).get("B", "a1", "play") is None
        assert EncounterStateRepository(session).get("B", "e1") is None
        assert EncounterStateRepository(session).active("B") is None
        assert CampaignClockRepository(session).get("B", "world").payload == {"value": 9}
        assert _states(session).list("B") == []
        with pytest.raises(RevisionConflict):
            _states(session).write("B", "a1", "play", read_revision=1, payload={})
        with pytest.raises(RevisionConflict):
            EncounterStateRepository(session).write("B", "e1", read_revision=1, round=2)
    assert _stored_state(sqlite_database, "A", "a1", "play").revision == 1


def test_campaign_wide_reads_return_frozen_records_in_key_order(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        for character_id in ("a2", "a1"):
            for domain in ("play", "build"):
                _insert_state(session, "A", character_id, domain)
        repo = EncounterStateRepository(session)
        _insert_encounter(session, "A", "e2", status="ended")
        _insert_encounter(session, "A", "e1")
        repo.add_combatant("A", "e2", read_revision=1, combatant_id="k9", kind="pc",
                           character_id="a2")
        repo.add_combatant("A", "e1", read_revision=1, combatant_id="k5", kind="pc",
                           character_id="a1")
        repo.add_combatant("A", "e1", read_revision=2, combatant_id="k1",
                           kind="creature", creature_ref="bestiary/goblin")
        clocks = CampaignClockRepository(session)
        clocks.insert("A", "world")
        clocks.insert("A", "downtime")
    with sqlite_database.session() as session:
        repo = EncounterStateRepository(session)
        states = _states(session).list("A")
        assert [(r.id, r.kind) for r in states] == [
            ("a1", "character.build"), ("a1", "character.play"),
            ("a2", "character.build"), ("a2", "character.play")]
        assert [r.id for r in repo.list("A")] == ["e1", "e2"]
        assert repo.active("A").id == "e1"
        assert [(r.id, r.revision) for r in repo.combatants("A", "e1")] == [("k1", 3), ("k5", 3)]
        assert [(r.payload["encounter_id"], r.id) for r in repo.list_combatants("A")] == [
            ("e1", "k1"), ("e1", "k5"), ("e2", "k9")]
        assert repo.is_combatant("A", "a1") is True
        assert repo.is_combatant("A", "a2") is False  # combatant of an ended encounter
        assert repo.is_combatant("B", "a1") is False
        assert [r.id for r in CampaignClockRepository(session).list("A")] == ["downtime", "world"]
        with pytest.raises(dataclasses.FrozenInstanceError):
            states[0].revision = 99


def test_records_do_not_alias_stored_json(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        payload = {"conditions": {"frightened": 1}}
        _insert_state(session, "A", "a1", "play", payload)
        payload["conditions"]["frightened"] = 3
        record = _states(session).get("A", "a1", "play")
        record.payload["conditions"]["frightened"] = 2
        assert _states(session).get("A", "a1", "play").payload == {
            "conditions": {"frightened": 1}}


# --------------------------------------------------------------------------
# pins and profiles (Decision 5)


def test_profile_mismatch_refuses_before_any_write(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        with pytest.raises(ProfileMismatch):
            _states(session).insert("A", "a1", "play", rules_profile="dnd5e-2024",
                                    content={}, payload={})
        with pytest.raises(ProfileMismatch):
            EncounterStateRepository(session).insert("A", "e1", rules_profile="dnd5e-2014",
                                                     content={})
        _insert_state(session, "A", "a1", "play")
        _insert_encounter(session, "A", "e1")
        with pytest.raises(ProfileMismatch):
            _states(session).write("A", "a1", "play", read_revision=1, payload={},
                                   rules_profile="dnd5e-2024")
        with pytest.raises(ProfileMismatch):
            EncounterStateRepository(session).write("A", "e1", read_revision=1,
                                                    rules_profile="dnd5e-2024")
        assert _states(session).get("A", "a1", "play").revision == 1
        assert EncounterStateRepository(session).get("A", "e1").revision == 1
        assert EncounterStateRepository(session).list("A")[0].rules_profile == "pf2e"


SHA = "a" * 64
DND_CONTENT = {"data_version": "2026.10", "db_sha256": SHA}


@pytest.mark.parametrize("settings", [
    {}, {"rules": "pf2e"}, {"rules": {"profile": "pf3e", "content": CONTENT}},
    {"rules": {"profile": "pf2e"}}, {"rules": {"profile": "pf2e", "content": []}},
    # PF2e content of the wrong shape
    {"rules": {"profile": "pf2e", "content": {}}},
    {"rules": {"profile": "pf2e", "content": DND_CONTENT}},
    {"rules": {"profile": "pf2e", "content": {"ruleset_id": [], "manifest_sha256": {}}}},
    {"rules": {"profile": "pf2e", "content": {"ruleset_id": "", "manifest_sha256": SHA}}},
    {"rules": {"profile": "pf2e", "content": {"ruleset_id": "core", "manifest_sha256": None}}},
    {"rules": {"profile": "pf2e", "content": {"ruleset_id": "core", "manifest_sha256": "abc"}}},
    {"rules": {"profile": "pf2e", "content": {"ruleset_id": "core",
                                              "manifest_sha256": "A" * 64}}},
    {"rules": {"profile": "pf2e", "content": {**CONTENT, "extra": 1}}},
    # D&D content of the wrong shape
    {"rules": {"profile": "dnd5e-2014", "content": CONTENT}},
    {"rules": {"profile": "dnd5e-2024", "content": {}}},
    {"rules": {"profile": "dnd5e-2024", "content": {"data_version": "", "db_sha256": SHA}}},
    {"rules": {"profile": "dnd5e-2024", "content": {"data_version": 7, "db_sha256": SHA}}},
    {"rules": {"profile": "dnd5e-2014", "content": {"data_version": "v", "db_sha256": None}}},
    {"rules": {"profile": "dnd5e-2014", "content": {**DND_CONTENT, "extra": 1}}},
])
def test_missing_or_malformed_pin_is_unavailable(sqlite_database, settings):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        session.execute(update(Campaign).where(Campaign.id == "A").values(settings=settings))
    with sqlite_database.transaction() as session:
        with pytest.raises(PinUnavailable):
            CampaignPinRepository(session).pin("A")
        with pytest.raises(PinUnavailable):
            _insert_state(session, "A", "a1", "play")
        with pytest.raises(PinUnavailable):
            _insert_encounter(session, "A", "e1")
        with pytest.raises(PinUnavailable):
            CampaignPinRepository(session).pin("missing-campaign")
        assert CampaignPinRepository(session).pin("B") == PIN
    assert _all_rows(sqlite_database, CharacterState.__table__) == []
    assert _all_rows(sqlite_database, EncounterState.__table__) == []


def test_malformed_pin_refuses_writes_to_existing_rows(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        _insert_state(session, "A", "a1", "play")
        _insert_encounter(session, "A", "e1")
        session.execute(update(Campaign).where(Campaign.id == "A").values(
            settings={"rules": {"profile": "pf2e", "content": {}}}))
    before = (_all_rows(sqlite_database, CharacterState.__table__),
              _all_rows(sqlite_database, EncounterState.__table__))
    with sqlite_database.transaction() as session:
        with pytest.raises(PinUnavailable):
            _states(session).write("A", "a1", "play", read_revision=1, payload={})
        with pytest.raises(PinUnavailable):
            EncounterStateRepository(session).write("A", "e1", read_revision=1, round=2)
        with pytest.raises(PinUnavailable):
            EncounterStateRepository(session).add_combatant(
                "A", "e1", read_revision=1, combatant_id="k", kind="creature")
    assert before == (_all_rows(sqlite_database, CharacterState.__table__),
                      _all_rows(sqlite_database, EncounterState.__table__))


@pytest.mark.parametrize("rules", [
    PIN,
    {"profile": "pf2e", "content": {"ruleset_id": "pf2e-remaster", "manifest_sha256": SHA}},
    {"profile": "dnd5e-2014", "content": DND_CONTENT},
    {"profile": "dnd5e-2024", "content": DND_CONTENT},
])
def test_well_formed_pins_are_accepted(sqlite_database, rules):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        session.execute(update(Campaign).where(Campaign.id == "A").values(
            settings={"rules": rules}))
    with sqlite_database.transaction() as session:
        assert CampaignPinRepository(session).pin("A") == rules
        record = _states(session).insert("A", "a1", "play", rules_profile=rules["profile"],
                                         content=rules["content"], payload={})
        encounter = EncounterStateRepository(session).insert(
            "A", "e1", rules_profile=rules["profile"], content=rules["content"])
    assert (record.revision, record.content) == (1, rules["content"])
    assert (encounter.revision, encounter.rules_profile) == (1, rules["profile"])


# --------------------------------------------------------------------------
# schema versions (Decision 6)


def test_upgrade_registry_with_a_synthetic_kind(monkeypatch):
    monkeypatch.setitem(state_repositories.CURRENT_SCHEMA_VERSIONS, "synthetic", 3)
    monkeypatch.setattr(state_repositories, "SCHEMA_UPGRADES", {})
    state_repositories.register_schema_upgrade("synthetic", 1, lambda p: {**p, "v2": True})
    state_repositories.register_schema_upgrade("synthetic", 2, lambda p: {**p, "v3": True})
    assert state_repositories.upgrade_payload("synthetic", 1, {"a": 1}) == (
        3, {"a": 1, "v2": True, "v3": True})
    assert state_repositories.upgrade_payload("synthetic", 3, {"a": 1}) == (3, {"a": 1})
    with pytest.raises(UnknownSchemaVersion):
        state_repositories.upgrade_payload("synthetic", 4, {})
    with pytest.raises(ValueError):
        state_repositories.register_schema_upgrade("synthetic", 1, lambda p: p)


def test_older_version_upgrades_on_read_without_writing(sqlite_database, monkeypatch):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        _insert_state(session, "A", "a1", "play", {"hp": 7})
    monkeypatch.setitem(state_repositories.CURRENT_SCHEMA_VERSIONS, "character.play", 2)
    monkeypatch.setattr(state_repositories, "SCHEMA_UPGRADES",
                        {"character.play": {1: lambda p: {**p, "temp_hp": 0}}})
    with sqlite_database.transaction() as session:
        record = _states(session).get("A", "a1", "play")
    assert record.schema_version == 2 and record.payload == {"hp": 7, "temp_hp": 0}
    stored = _stored_state(sqlite_database, "A", "a1", "play")
    assert stored.schema_version == 1 and stored.payload == {"hp": 7}
    with sqlite_database.transaction() as session:
        _states(session).write("A", "a1", "play", read_revision=1, payload=record.payload)
    assert _stored_state(sqlite_database, "A", "a1", "play").schema_version == 2


def test_newer_stored_version_fails_closed(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        _insert_state(session, "A", "a1", "play")
        _insert_encounter(session, "A", "e1")
        session.execute(update(CharacterState).values(schema_version=2))
        session.execute(update(EncounterState).values(schema_version=2))
    with sqlite_database.transaction() as session:
        with pytest.raises(UnknownSchemaVersion):
            _states(session).get("A", "a1", "play")
        with pytest.raises(UnknownSchemaVersion):
            EncounterStateRepository(session).get("A", "e1")


def test_encounter_partial_writes_store_the_upgraded_state(sqlite_database, monkeypatch):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        _insert_encounter(session, "A", "e1", payload={"old": 1})
        _insert_encounter(session, "A", "e2", status="ended", payload={"old": 2})
    monkeypatch.setitem(state_repositories.CURRENT_SCHEMA_VERSIONS, "encounter", 2)
    monkeypatch.setattr(state_repositories, "SCHEMA_UPGRADES", {"encounter": {
        1: lambda state: {**state, "payload": {**state["payload"], "new": True}}}})
    with sqlite_database.transaction() as session:
        repo = EncounterStateRepository(session)
        assert repo.write("A", "e1", read_revision=1, round=3).payload["payload"] == {
            "old": 1, "new": True}
        repo.add_combatant("A", "e1", read_revision=2, combatant_id="k1", kind="creature")
        stored = session.execute(select(EncounterState.__table__)
                                 .where(EncounterState.encounter_id == "e1")).first()
    assert (stored.schema_version, stored.payload, stored.round, stored.revision) == (
        2, {"old": 1, "new": True}, 3, 3)
    # A combatant change on a still-old encounter (never written) upgrades it too.
    with sqlite_database.transaction() as session:
        EncounterStateRepository(session).add_combatant(
            "A", "e2", read_revision=1, combatant_id="k2", kind="creature")
        stored = session.execute(select(EncounterState.__table__)
                                 .where(EncounterState.encounter_id == "e2")).first()
    assert (stored.schema_version, stored.payload, stored.status, stored.revision) == (
        2, {"old": 2, "new": True}, "ended", 2)


def _all_rows(database, table):
    with database.session() as session:
        return [tuple(row) for row in session.execute(select(table)).all()]


@pytest.mark.parametrize("domain", ["build", "play"])
@pytest.mark.parametrize("operation", ["omitted_profile_write", "explicit_profile_write",
                                       "delete"])
def test_character_writes_over_a_newer_stored_version_fail_closed(
        sqlite_database, domain, operation):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        _insert_state(session, "A", "a1", domain, {"future_field": "must survive"})
        session.execute(update(CharacterState).values(schema_version=2))
    before = _all_rows(sqlite_database, CharacterState.__table__)
    with sqlite_database.transaction() as session:
        repo = _states(session)
        with pytest.raises(UnknownSchemaVersion):
            if operation == "delete":
                repo.delete("A", "a1", domain, read_revision=1)
            else:
                profile = {"rules_profile": "pf2e"} if operation.startswith("explicit") else {}
                repo.write("A", "a1", domain, read_revision=1, payload={"hp": 4}, **profile)
    # The whole row, revision, payload and timestamps included, is untouched.
    assert _all_rows(sqlite_database, CharacterState.__table__) == before


def test_character_write_and_delete_keep_compare_and_set(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        _insert_state(session, "A", "a1", "play")
        _states(session).write("A", "a1", "play", read_revision=1, payload={"hp": 8},
                               rules_profile="pf2e")
    before = _all_rows(sqlite_database, CharacterState.__table__)
    with sqlite_database.transaction() as session:
        with pytest.raises(RevisionConflict):
            _states(session).write("A", "a1", "play", read_revision=1, payload={},
                                   rules_profile="pf2e")
        with pytest.raises(RevisionConflict):
            _states(session).delete("A", "a1", "play", read_revision=1)
        with pytest.raises(RevisionConflict):
            _states(session).delete("A", "a2", "play", read_revision=1)
    assert _all_rows(sqlite_database, CharacterState.__table__) == before
    with sqlite_database.transaction() as session:
        _states(session).delete("A", "a1", "play", read_revision=2)
    assert _all_rows(sqlite_database, CharacterState.__table__) == []


def test_encounter_writes_over_a_newer_stored_version_fail_closed(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        _insert_encounter(session, "A", "e1")
        session.execute(update(EncounterState).values(schema_version=2))
    before = _all_rows(sqlite_database, EncounterState.__table__)
    with sqlite_database.transaction() as session:
        repo = EncounterStateRepository(session)
        with pytest.raises(UnknownSchemaVersion):
            repo.write("A", "e1", read_revision=1, round=2)
        with pytest.raises(UnknownSchemaVersion):
            repo.write("A", "e1", read_revision=1, round=2, rules_profile="pf2e")
        with pytest.raises(UnknownSchemaVersion):
            repo.add_combatant("A", "e1", read_revision=1, combatant_id="k", kind="creature")
    assert _all_rows(sqlite_database, EncounterState.__table__) == before


# --------------------------------------------------------------------------
# authority (Decision 4)


@pytest.mark.parametrize("authority", ["db", "exporting"])
def test_missing_state_row_in_db_or_exporting_raises(sqlite_database, authority):
    _seed(sqlite_database, {"A": authority, "B": "db"})
    with sqlite_database.transaction() as session:
        with pytest.raises(StateRowMissing):
            _states(session).get("A", "a1", "play")
        with pytest.raises(StateRowMissing):
            CampaignClockRepository(session).get("A", "world")
        assert _states(session).get("A", "b1", "play") is None
        assert CampaignClockRepository(session).get("A", "moon") is None


@pytest.mark.parametrize("authority", [None, "file", "migrating"])
def test_file_and_migrating_reads_need_allow_stale(sqlite_database, authority):
    _seed(sqlite_database, {"A": authority, "B": "db"})
    with sqlite_database.transaction() as session:
        session.add(CharacterState(campaign_id="A", character_id="a1", domain="play",
                                   schema_version=1, revision=4, rules_profile="pf2e",
                                   content=CONTENT, payload={"hp": 2}))
        session.add(CampaignClock(campaign_id="A", clock="world", value=1, revision=1))
    with sqlite_database.transaction() as session:
        assert StateAuthorityRepository(session).authority("A") == (authority or "file")
        reads = [
            lambda **kw: _states(session).get("A", "a1", "play", **kw),
            lambda **kw: _states(session).list("A", **kw),
            lambda **kw: EncounterStateRepository(session).active("A", **kw),
            lambda **kw: EncounterStateRepository(session).list_combatants("A", **kw),
            lambda **kw: EncounterStateRepository(session).is_combatant("A", "a1", **kw),
            lambda **kw: CampaignClockRepository(session).get("A", "world", **kw),
        ]
        for read in reads:
            with pytest.raises(AuthorityMismatch):
                read()
            read(allow_stale=True)
        assert _states(session).get("A", "a1", "play", allow_stale=True).revision == 4
        assert _states(session).get("A", "a2", "play", allow_stale=True) is None


@pytest.mark.parametrize("authority", ["file", "migrating", "exporting"])
def test_writes_need_db_authority(sqlite_database, authority):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        _insert_state(session, "A", "a1", "play")
        _insert_encounter(session, "A", "e1")
        CampaignClockRepository(session).insert("A", "world")
    _set_authority(sqlite_database, "A", authority)
    with sqlite_database.transaction() as session:
        encounters = EncounterStateRepository(session)
        writes = [
            lambda: _insert_state(session, "A", "a2", "play"),
            lambda: _states(session).write("A", "a1", "play", read_revision=1, payload={}),
            lambda: _states(session).delete("A", "a1", "play", read_revision=1),
            lambda: _insert_encounter(session, "A", "e2", status="ended"),
            lambda: encounters.write("A", "e1", read_revision=1, round=3),
            lambda: encounters.add_combatant("A", "e1", read_revision=1, combatant_id="k",
                                             kind="creature"),
            lambda: CampaignClockRepository(session).write("A", "world", read_revision=1,
                                                           value=4),
            lambda: CampaignClockRepository(session).insert("A", "downtime"),
        ]
        for write in writes:
            with pytest.raises(AuthorityMismatch):
                write()
    _set_authority(sqlite_database, "A", "db")
    with sqlite_database.session() as session:
        assert [r.revision for r in _states(session).list("A")] == [1]
        assert [r.revision for r in EncounterStateRepository(session).list("A")] == [1]
        assert EncounterStateRepository(session).list_combatants("A") == []
        assert [(r.id, r.revision) for r in CampaignClockRepository(session).list("A")] == [
            ("world", 1)]


# --------------------------------------------------------------------------
# CommandLocks (Decision 3)


def test_command_locks_requires_lock_stream(sqlite_database):
    with sqlite_database.session() as session:
        with pytest.raises(TypeError):
            CommandLocks(session, "A")
        with pytest.raises(TypeError):
            CommandLocks(session, "A", lock_stream=None)


def test_command_locks_stream_first(sqlite_database):
    _seed(sqlite_database)
    calls = []
    with sqlite_database.transaction() as session:
        locks = CommandLocks(session, "A", lock_stream=_stub_stream(calls))
        attempts = [
            lambda: locks.stream_row,
            lambda: locks.encounter("e1"),
            lambda: locks.character_states([("a1", "play")]),
            lambda: locks.character_states([]),
            lambda: locks.records_for("campaign_clocks", ["world"]),
            lambda: locks.register_serialized("campaign", "A", None),
        ]
        for attempt in attempts:
            with pytest.raises(LockOrderError):
                attempt()
        assert locks.stream() == {"campaign_id": "A"}
        assert locks.stream_row == {"campaign_id": "A"}
        with pytest.raises(LockOrderError):
            locks.stream()
    assert len(calls) == 1 and calls[0][1] == "A"
    assert locks.session is session and locks.campaign_id == "A"


def test_character_states_lock_in_ascending_order_whatever_the_caller_order(
        sqlite_database, monkeypatch):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        for character_id in ("a1", "a2"):
            for domain in ("build", "play"):
                _insert_state(session, "A", character_id, domain)
    order = []
    original = CharacterStateRepository.get

    def spy(self, campaign_id, character_id, domain, **kw):
        assert kw == {"for_update": True}
        order.append((character_id, domain))
        return original(self, campaign_id, character_id, domain, **kw)

    monkeypatch.setattr(CharacterStateRepository, "get", spy)
    with sqlite_database.transaction() as session:
        locks = CommandLocks(session, "A", lock_stream=_stub_stream([]))
        locks.stream()
        locked = locks.character_states([("a2", "play"), ("a1", "play"), ("a2", "build"),
                                         ("a1", "play")])
    assert order == [("a1", "play"), ("a2", "build"), ("a2", "play")]
    assert list(locked) == order
    assert locks.records[("character.build", "a2")].revision == 1


def test_lock_order_refuses_backward_steps_and_cross_call_regressions(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        for character_id in ("a1", "a2"):
            _insert_state(session, "A", character_id, "play")
        _insert_encounter(session, "A", "e1")
        CampaignClockRepository(session).insert("A", "world")

    def fresh(session):
        locks = CommandLocks(session, "A", lock_stream=_stub_stream([]))
        locks.stream()
        return locks

    with sqlite_database.transaction() as session:
        locks = fresh(session)
        locks.character_states([("a2", "play")])
        with pytest.raises(LockOrderError):
            locks.character_states([("a1", "play")])  # (B, play) then (A, play)
        with pytest.raises(LockOrderError):
            locks.character_states([("a2", "play")])  # repeat of a held key
        with pytest.raises(LockOrderError):
            locks.encounter("e1")  # step 2 after step 4
        locks.records_for("campaign_clocks", ["world"])
        with pytest.raises(LockOrderError):
            locks.character_states([("a3", "play")])  # step 4 after step 5

    with sqlite_database.transaction() as session:
        locks = fresh(session)
        locks.encounter("e1")
        locks.character_states([("a1", "play")])
        locks.character_states([("a2", "play")])  # ascending across calls is fine
        with pytest.raises(LockOrderError):
            locks.records_for("not_a_table", ["x"])
        with pytest.raises(LockOrderError):
            locks.actions("not_an_action_table", ["x"])


def test_step5_tables_lock_in_registered_order(sqlite_database, monkeypatch):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        CampaignClockRepository(session).insert("A", "world")
        CampaignClockRepository(session).insert("A", "downtime")
    monkeypatch.setattr(command_locks, "_STEP5_TABLES", list(command_locks._STEP5_TABLES))
    seen = []

    def read_item(session, campaign_id, item_id):
        seen.append(item_id)
        return StateRecord.serialized("item", item_id, {"campaign_id": campaign_id})

    command_locks.register_lock_table("item_instances", kind="item", read=read_item)
    with pytest.raises(ValueError):
        command_locks.register_lock_table("item_instances", kind="item", read=read_item)
    with sqlite_database.transaction() as session:
        locks = CommandLocks(session, "A", lock_stream=_stub_stream([]))
        locks.stream()
        clocks = locks.records_for("campaign_clocks", ["world", "downtime"])
        assert list(clocks) == ["downtime", "world"]
        locks.records_for("item_instances", ["i2", "i1"])
        assert seen == ["i1", "i2"] and locks.records[("item", "i1")].id == "i1"
        with pytest.raises(LockOrderError):
            locks.records_for("campaign_clocks", ["world"])
        with pytest.raises(LockOrderError):
            locks.records_for("item_instances", ["i0"])
        locks.records_for("item_instances", ["i3"])


def test_command_locks_campaign_scoped(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        _insert_state(session, "B", "b1", "play")
        clocks = CampaignClockRepository(session)
        clocks.insert("A", "world", value=1)
        clocks.insert("B", "world", value=2)
        clocks.write("B", "world", read_revision=1, value=3)
    calls = []
    with sqlite_database.transaction() as session:
        spy = _stub_stream(calls)
        locks = CommandLocks(session, "A", lock_stream=spy)
        locks.stream()
        assert locks.character_states([("b1", "play")]) == {("b1", "play"): None}
        assert locks.records[("character.play", "b1")] is None
        world = locks.records_for("campaign_clocks", ["moon", "world"])
        assert world["moon"] is None and locks.records[("clock", "moon")] is None
        assert world["world"].revision == 1 and world["world"].payload == {"value": 1}
    assert [(s is session, cid) for s, cid in calls] == [(True, "A")]
    with sqlite_database.transaction() as session:
        session.query(CampaignClock).filter_by(campaign_id="A", clock="world").delete()
    calls.clear()
    with sqlite_database.transaction() as session:
        locks = CommandLocks(session, "A", lock_stream=_stub_stream(calls))
        locks.stream()
        with pytest.raises(StateRowMissing):
            locks.records_for("campaign_clocks", ["world"])
        assert ("clock", "world") not in locks.records
    assert [cid for _, cid in calls] == ["A"]


def test_command_locks_missing_state_row_raises(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        locks = CommandLocks(session, "A", lock_stream=_stub_stream([]))
        locks.stream()
        with pytest.raises(StateRowMissing):
            locks.character_states([("a1", "play")])
    with sqlite_database.transaction() as session:
        locks = CommandLocks(session, "A", lock_stream=_stub_stream([]))
        locks.stream()
        assert locks.character_states([("b1", "play")])[("b1", "play")] is None


@pytest.mark.parametrize("authority", ["file", "migrating"])
def test_command_locks_never_read_stale_rows(sqlite_database, authority):
    _seed(sqlite_database, {"A": authority, "B": "db"})
    with sqlite_database.transaction() as session:
        locks = CommandLocks(session, "A", lock_stream=_stub_stream([]))
        locks.stream()
        with pytest.raises(AuthorityMismatch):
            locks.character_states([("a1", "play")])


def test_encounter_lock_registers_combatants(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        repo = EncounterStateRepository(session)
        _insert_encounter(session, "A", "e1")
        _insert_encounter(session, "A", "e2", status="ended")
        repo.add_combatant("A", "e1", read_revision=1, combatant_id="k1", kind="pc",
                           character_id="a1")
        repo.add_combatant("A", "e1", read_revision=2, combatant_id="k2",
                           kind="creature", creature_ref="bestiary/goblin")
        repo.add_combatant("A", "e2", read_revision=1, combatant_id="k3",
                           kind="creature", creature_ref="bestiary/orc")
    with sqlite_database.transaction() as session:
        locks = CommandLocks(session, "A", lock_stream=_stub_stream([]))
        locks.stream()
        record = locks.encounter("e1")
        assert record.revision == 3
        assert set(locks.records) == {("encounter", "e1"), ("combatant", "k1"),
                                      ("combatant", "k2")}
        assert locks.records[("combatant", "k1")].payload["character_id"] == "a1"
        assert locks.records[("combatant", "k2")].revision == 3
    with sqlite_database.transaction() as session:
        locks = CommandLocks(session, "B", lock_stream=_stub_stream([]))
        locks.stream()
        assert locks.encounter("e1") is None
        assert dict(locks.records) == {("encounter", "e1"): None}


def test_register_serialized_allowlist(sqlite_database, monkeypatch):
    monkeypatch.setattr(command_locks, "STREAM_SERIALIZED_KINDS",
                        command_locks.STREAM_SERIALIZED_KINDS)
    with sqlite_database.transaction() as session:
        locks = CommandLocks(session, "A", lock_stream=_stub_stream([]))
        with pytest.raises(LockOrderError):
            locks.register_serialized("campaign", "A", None)
        locks.stream()
        for kind in ("campaign", "session", "grant"):
            locks.register_serialized(kind, "x1", StateRecord.serialized(kind, "x1", {"k": 1}))
            locks.register_serialized(kind, "x2", None)
            assert locks.records[(kind, "x1")].payload == {"k": 1}
            assert locks.records[(kind, "x1")].revision is None
            assert locks.records[(kind, "x2")] is None
        for kind in ("character.play", "clock", "invitation"):
            with pytest.raises(LockOrderError):
                locks.register_serialized(kind, "x", None)
        with pytest.raises(ValueError):
            locks.register_serialized("grant", "x3", StateRecord.serialized("grant", "x9", {}))
        command_locks.register_stream_serialized_kind("invitation")
        locks.register_serialized("invitation", "x", None)
        assert locks.records[("invitation", "x")] is None
        with pytest.raises(TypeError):
            locks.records[("grant", "x9")] = None


def test_command_locks_keep_no_module_state(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        _insert_state(session, "A", "a1", "play")
    with sqlite_database.transaction() as session:
        first = CommandLocks(session, "A", lock_stream=_stub_stream([]))
        first.stream()
        first.character_states([("a1", "play")])
        second = CommandLocks(session, "A", lock_stream=_stub_stream([]))
        second.stream()
        second.character_states([("a1", "play")])  # no state shared with `first`
        assert dict(second.records) == dict(first.records)
