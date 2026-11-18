from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Flask

from core import obsidian_sync, storage
from services.obsidian_sync import register_obsidian_sync


CID = "b" * 32


class FakeAdapter:
    def __init__(self):
        self.active_id = CID
        self.round = 1
        self.turn_index = 0
        self.pc_hp = 30
        self.combatants = [SimpleNamespace(
            instance_id="hero-1",
            name="Hero",
            is_pc=True,
            initiative=18,
            current_hp=30,
            hp=40,
            conditions={},
        )]

    def find(self, target_id):
        return next((c for c in self.combatants if c.instance_id == target_id), None)

    def snapshot(self):
        active = self.combatants[self.turn_index] if self.combatants else None
        return {
            "campaign": {"id": CID, "name": "Test Campaign", "system": "pf2e"},
            "party": [{
                "name": "Hero", "current_hp": self.pc_hp, "max_hp": 40,
                "temp_hp": 0, "conditions": {},
            }],
            "encounter": {
                "round": self.round,
                "turn_index": self.turn_index,
                "active_id": active.instance_id if active else None,
                "active_name": active.name if active else None,
                "combatants": [{
                    "instance_id": c.instance_id,
                    "name": c.name,
                    "is_pc": c.is_pc,
                    "initiative": c.initiative,
                    "current_hp": c.current_hp,
                    "max_hp": c.hp,
                    "conditions": dict(c.conditions),
                } for c in self.combatants],
            },
        }

    def apply_hp(self, target_id, amount, action, _damage_type):
        target = self.find(target_id)
        if target is None:
            return None
        old = target.current_hp
        if action == "damage":
            target.current_hp = max(0, target.current_hp - amount)
        else:
            target.current_hp = min(target.hp, target.current_hp + amount)
        self.pc_hp = target.current_hp
        return old

    def adjust_party_hp(self, pc_name, amount, action):
        if pc_name != "Hero":
            return None
        old = self.pc_hp
        self.pc_hp = max(0, self.pc_hp - amount) if action == "damage" else min(40, self.pc_hp + amount)
        self.combatants[0].current_hp = self.pc_hp
        return {"pc_name": pc_name, "old_hp": old, "new_hp": self.pc_hp, "action": action}

    def condition(self, target_id, condition, action, _rounds):
        target = self.find(target_id)
        if target is None:
            return False
        current = target.conditions.get(condition, 0)
        if action in ("increase", "add"):
            target.conditions[condition] = current + 1
        elif action == "decrease":
            target.conditions[condition] = max(0, current - 1)
        elif action == "toggle":
            target.conditions[condition] = not bool(current)
        elif action == "remove":
            target.conditions[condition] = False
        return True

    def sort(self):
        self.combatants.sort(key=lambda c: c.initiative, reverse=True)

    def advance(self, direction):
        if direction == "next":
            self.turn_index = (self.turn_index + 1) % len(self.combatants)
            if self.turn_index == 0:
                self.round += 1
        else:
            self.turn_index = (self.turn_index - 1) % len(self.combatants)

    def callbacks(self):
        return {
            "active_campaign_id": lambda: self.active_id,
            "campaign_doc": lambda _cid: {"id": CID, "name": "Test Campaign", "system": "pf2e"},
            "snapshot": self.snapshot,
            "find_combatant": self.find,
            "apply_hp": self.apply_hp,
            "maybe_remove_defeated": lambda *_args: None,
            "adjust_party_hp": self.adjust_party_hp,
            "apply_condition": self.condition,
            "sort_encounter": self.sort,
            "persist_encounter": lambda: None,
            "broadcast_encounter": lambda: None,
            "advance_turn": self.advance,
            "has_encounter": lambda: bool(self.combatants),
            "resolve_roll": lambda payload: {
                "roll_id": "roll-test", "actor_name": "Hero",
                "label": payload.get("roll_kind", "check"),
                "formula": "1d20+8", "d20": 12, "modifier": 8,
                "total": 20, "dc": payload.get("dc"), "degree": "success",
                "visibility": "gm",
            },
            "launch_room_encounter": lambda payload: {
                "area_id": payload.get("area_id"), "combatant_count": len(self.combatants),
            },
            "sync_room_reminders": lambda area_id, reminders: {
                "area_id": area_id, "reminder_count": len(reminders),
            },
            "create_player_reveal": lambda payload: {
                "id": "handout-1", "title": payload.get("title"),
                "content": payload.get("content"), "recipients": payload.get("recipients"),
            },
            "combatant_detail": lambda instance_id: (
                {"name": "Hero", "level": 1, "is_pc": True, "ac": 18, "attacks": [], "skills": []}
                if instance_id == "hero-1" else None
            ),
            "add_creatures": lambda payload: (
                {"added": [str(c.get("name")) for c in payload.get("creatures", [])
                           if str(c.get("name", "")).lower() == "desmohund"],
                 "unresolved": [{"name": c.get("name"), "candidates": []}
                                for c in payload.get("creatures", [])
                                if str(c.get("name", "")).lower() != "desmohund"],
                 "combatant_count": 2}
            ),
            "gm_required": lambda fn: fn,
        }


@pytest.fixture
def sync_client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CAMPAIGNS_DIR", str(tmp_path / "campaigns"))
    storage.ensure_campaign_dirs(CID)
    fake = FakeAdapter()
    flask_app = Flask(__name__)
    flask_app.secret_key = "test"
    register_obsidian_sync(flask_app, fake.callbacks())
    raw_token, _ = obsidian_sync.create_token(CID, "tests")
    with flask_app.test_client() as client:
        yield client, fake, raw_token


def headers(token):
    return {"Authorization": f"Bearer {token}", "X-Campaign-ID": CID}


def test_state_requires_valid_campaign_token(sync_client):
    client, fake, raw_token = sync_client
    assert client.get("/api/integrations/obsidian/v1/state").status_code == 401
    assert client.get("/api/integrations/obsidian/v1/state", headers=headers("obs1_bad_token")).status_code == 401
    assert client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token)).status_code == 200
    fake.active_id = "c" * 32
    assert client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token)).status_code == 409


def test_command_is_revisioned_idempotent_and_evented(sync_client):
    client, fake, raw_token = sync_client
    initial = client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token)).get_json()
    assert initial["revision"] == 0

    command = {
        "command_id": "damage-command-001",
        "expected_revision": 0,
        "type": "adjust_hp",
        "payload": {"target_id": "hero-1", "amount": 7, "action": "damage"},
    }
    first = client.post("/api/integrations/obsidian/v1/commands", headers=headers(raw_token), json=command)
    assert first.status_code == 200
    assert first.get_json()["revision"] == 1
    assert fake.pc_hp == 23

    replay = client.post("/api/integrations/obsidian/v1/commands", headers=headers(raw_token), json=command)
    assert replay.status_code == 200
    assert replay.get_json()["idempotent_replay"] is True
    assert fake.pc_hp == 23

    stale = dict(command, command_id="damage-command-002")
    assert client.post("/api/integrations/obsidian/v1/commands", headers=headers(raw_token), json=stale).status_code == 409

    events = client.get("/api/integrations/obsidian/v1/events?after=0", headers=headers(raw_token)).get_json()["events"]
    assert len(events) == 1
    assert events[0]["command_type"] == "adjust_hp"
    assert events[0]["before"]["target"]["current_hp"] == 30
    assert events[0]["after"]["target"]["current_hp"] == 23


def test_website_side_change_advances_observed_revision(sync_client):
    client, fake, raw_token = sync_client
    first = client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token)).get_json()
    fake.pc_hp = 19
    fake.combatants[0].current_hp = 19
    second = client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token)).get_json()
    assert second["revision"] == first["revision"] + 1
    assert second["state"]["party"][0]["current_hp"] == 19


def test_session_lifecycle_is_recorded(sync_client):
    client, _fake, raw_token = sync_client
    base = client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token)).get_json()
    started = client.post("/api/integrations/obsidian/v1/sessions/start", headers=headers(raw_token), json={
        "command_id": "session-start-001",
        "expected_revision": base["revision"],
        "payload": {"session_id": "session-s7", "label": "Session 7"},
    })
    assert started.status_code == 200
    assert started.get_json()["session"]["status"] == "live"

    ended = client.post("/api/integrations/obsidian/v1/sessions/end", headers=headers(raw_token), json={
        "command_id": "session-end-001",
        "expected_revision": started.get_json()["revision"],
        "payload": {"session_id": "session-s7"},
    })
    assert ended.status_code == 200
    assert ended.get_json()["session"]["status"] == "awaiting_processing"
    assert ended.get_json()["session"]["ended_at"]


def test_roll_room_and_reveal_commands_are_structured(sync_client):
    client, _fake, raw_token = sync_client
    initial = client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token)).get_json()

    rolled = client.post("/api/integrations/obsidian/v1/commands", headers=headers(raw_token), json={
        "command_id": "roll-command-001",
        "expected_revision": initial["revision"],
        "type": "roll",
        "payload": {"target_id": "hero-1", "roll_kind": "perception", "dc": 18},
    })
    assert rolled.status_code == 200
    assert rolled.get_json()["result"]["total"] == 20

    entered = client.post("/api/integrations/obsidian/v1/commands", headers=headers(raw_token), json={
        "command_id": "room-command-001",
        "expected_revision": rolled.get_json()["revision"],
        "type": "set_active_room",
        "payload": {"area_id": "sob-b1-c10", "title": "C10 Courtyard", "path": "C10.md"},
    })
    assert entered.status_code == 200
    state = entered.get_json()["state"]
    assert state["operations"]["active_room"]["area_id"] == "sob-b1-c10"

    revealed = client.post("/api/integrations/obsidian/v1/commands", headers=headers(raw_token), json={
        "command_id": "reveal-command-001",
        "expected_revision": entered.get_json()["revision"],
        "type": "create_player_reveal",
        "payload": {"title": "Inscription", "content": "The door opens.", "recipients": ["all"]},
    })
    assert revealed.status_code == 200
    assert revealed.get_json()["result"]["reveal"]["title"] == "Inscription"
    assert revealed.get_json()["state"]["operations"]["reveal_history"][-1]["id"] == "handout-1"

    events = client.get("/api/integrations/obsidian/v1/events?after=0", headers=headers(raw_token)).get_json()["events"]
    assert [event["type"] for event in events] == ["roll", "room_entered", "player_reveal"]


def test_player_request_is_durable_and_resolvable(sync_client):
    client, _fake, raw_token = sync_client
    created = obsidian_sync.append_player_request(CID, {
        "kind": "whisper", "pc_name": "Hero", "text": "Can I inspect the altar?",
    })
    state = client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token)).get_json()
    assert state["state"]["operations"]["player_requests"][0]["status"] == "open"

    resolved = client.post("/api/integrations/obsidian/v1/commands", headers=headers(raw_token), json={
        "command_id": "request-command-001",
        "expected_revision": state["revision"],
        "type": "resolve_player_request",
        "payload": {"request_id": created["id"], "action": "resolve", "resolution": "Perception check"},
    })
    assert resolved.status_code == 200
    item = resolved.get_json()["result"]["player_request"]
    assert item["status"] == "resolved"
    assert item["resolution"] == "Perception check"


# ---------------------------------------------------------------------------
# Undo and multi-target damage
# ---------------------------------------------------------------------------

def _command(client, raw_token, cid, kind, payload, revision):
    return client.post(
        "/api/integrations/obsidian/v1/commands",
        headers=headers(raw_token),
        json={"command_id": cid, "expected_revision": revision, "type": kind, "payload": payload},
    )


def test_undo_reverses_damage_by_the_amount_actually_applied(sync_client):
    """The inverse must use the recorded old/new HP, not the requested amount --
    resistances and the max-HP ceiling mean those differ."""
    client, fake, raw_token = sync_client
    client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token))
    start = fake.combatants[0].current_hp

    r = _command(client, raw_token, "undo-dmg-0001", "adjust_hp",
                 {"target_id": "hero-1", "amount": 9, "action": "damage"}, 0)
    assert r.status_code == 200, r.get_json()
    assert fake.combatants[0].current_hp == start - 9
    rev = r.get_json()["revision"]

    undo = _command(client, raw_token, "undo-cmd-0001", "undo_last", {}, rev)
    assert undo.status_code == 200, undo.get_json()
    assert fake.combatants[0].current_hp == start, "undo did not restore the HP"
    assert undo.get_json()["result"]["command_type"] == "adjust_hp"


def test_an_undo_cannot_itself_be_undone(sync_client):
    """Otherwise a second tap redoes the thing you just reversed, which reads as
    the button doing nothing while quietly toggling state."""
    client, fake, raw_token = sync_client
    client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token))
    r = _command(client, raw_token, "undo-dmg-0002", "adjust_hp",
                 {"target_id": "hero-1", "amount": 4, "action": "damage"}, 0)
    rev = r.get_json()["revision"]
    undo = _command(client, raw_token, "undo-cmd-0002", "undo_last", {}, rev)
    assert undo.status_code == 200
    again = _command(client, raw_token, "undo-cmd-0003", "undo_last", {},
                     undo.get_json()["revision"])
    assert again.status_code == 400
    assert "itself an undo" in again.get_json()["error"]


def test_undo_refuses_a_command_it_cannot_honestly_reverse(sync_client):
    """A captured note is already written into the vault; pretending to undo it
    would be a lie. Refuse by name rather than approximate."""
    client, fake, raw_token = sync_client
    client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token))
    r = _command(client, raw_token, "note-cmd-0001", "capture_note",
                 {"text": "the cultist fled north"}, 0)
    assert r.status_code == 200
    undo = _command(client, raw_token, "undo-cmd-0004", "undo_last", {},
                    r.get_json()["revision"])
    assert undo.status_code == 400
    assert "cannot be undone" in undo.get_json()["error"]


def test_undo_with_nothing_to_undo_is_refused(sync_client):
    client, fake, raw_token = sync_client
    client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token))
    undo = _command(client, raw_token, "undo-cmd-0005", "undo_last", {}, 0)
    assert undo.status_code == 400
    assert "nothing to undo" in undo.get_json()["error"]


def test_multi_target_damage_applies_once_per_target_in_one_command(sync_client):
    """One command, one revision bump, one event -- so one undo takes back the
    whole area effect rather than a quarter of it."""
    client, fake, raw_token = sync_client
    fake.combatants.append(SimpleNamespace(
        instance_id="hero-2", name="Second", is_pc=True, initiative=12,
        current_hp=30, hp=40, conditions={},
    ))
    client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token))
    before = {c.instance_id: c.current_hp for c in fake.combatants}

    r = _command(client, raw_token, "aoe-cmd-0001", "adjust_hp",
                 {"target_ids": ["hero-1", "hero-2"], "amount": 6, "action": "damage"}, 0)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()["result"]
    assert len(body["targets"]) == 2
    for c in fake.combatants:
        assert c.current_hp == before[c.instance_id] - 6

    events = client.get("/api/integrations/obsidian/v1/events?after=0",
                        headers=headers(raw_token)).get_json()["events"]
    assert len([e for e in events if e["command_type"] == "adjust_hp"]) == 1, \
        "an area effect must be one event, not one per target"

    undo = _command(client, raw_token, "aoe-undo-0001", "undo_last", {}, r.get_json()["revision"])
    assert undo.status_code == 200, undo.get_json()
    for c in fake.combatants:
        assert c.current_hp == before[c.instance_id], "undo left a target un-reversed"


def test_multi_target_is_all_or_nothing_on_a_missing_combatant(sync_client):
    """Applying to some and failing would leave the GM unsure which half landed."""
    client, fake, raw_token = sync_client
    client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token))
    before = fake.combatants[0].current_hp
    r = _command(client, raw_token, "aoe-cmd-0002", "adjust_hp",
                 {"target_ids": ["hero-1", "ghost-9"], "amount": 5, "action": "damage"}, 0)
    assert r.status_code == 404
    assert fake.combatants[0].current_hp == before, "a target was damaged despite the failure"


def test_combatant_detail_is_served_on_demand_not_in_the_snapshot(sync_client):
    """The statblock is the largest and least volatile thing the pane needs, so
    it must not ride the 1 Hz /state poll on the worker that also carries every
    player's SSE."""
    client, fake, raw_token = sync_client
    r = client.get("/api/integrations/obsidian/v1/combatant/hero-1", headers=headers(raw_token))
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["instance_id"] == "hero-1"
    assert body["detail"]["ac"] == 18


def test_combatant_detail_requires_the_bearer_token(sync_client):
    """It is on the bearer blueprint, not behind the GM session gate that
    /api/combatant_stats uses -- the pane has no session cookie."""
    client, fake, raw_token = sync_client
    assert client.get("/api/integrations/obsidian/v1/combatant/hero-1").status_code == 401
    assert client.get("/api/integrations/obsidian/v1/combatant/hero-1",
                      headers=headers("obs1_bad_token")).status_code == 401


def test_combatant_detail_404s_for_an_unknown_combatant(sync_client):
    client, fake, raw_token = sync_client
    r = client.get("/api/integrations/obsidian/v1/combatant/ghost-9", headers=headers(raw_token))
    assert r.status_code == 404
    assert r.get_json()["ok"] is False


def test_a_live_room_can_be_left(sync_client):
    """Entering is one tap from any note with an area_id, so mis-taps happen.
    Before this there was no way back to 'nowhere in particular'."""
    client, fake, raw_token = sync_client
    client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token))
    entered = _command(client, raw_token, "room-enter-0001", "set_active_room",
                       {"area_id": "sob-c11", "title": "C11 Hound Pens"}, 0)
    assert entered.status_code == 200, entered.get_json()
    state = client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token)).get_json()
    assert state["state"]["operations"]["active_room"]["area_id"] == "sob-c11"

    left = _command(client, raw_token, "room-leave-0001", "clear_active_room", {},
                    entered.get_json()["revision"])
    assert left.status_code == 200, left.get_json()
    assert left.get_json()["result"]["cleared"]["area_id"] == "sob-c11"
    after = client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token)).get_json()
    assert after["state"]["operations"]["active_room"] is None


def test_leaving_when_no_room_is_live_is_refused(sync_client):
    client, fake, raw_token = sync_client
    client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token))
    r = _command(client, raw_token, "room-leave-0002", "clear_active_room", {}, 0)
    assert r.status_code == 400
    assert "no room is currently live" in r.get_json()["error"]


def test_creatures_named_in_a_note_can_be_added_to_the_encounter(sync_client):
    """The room note names creatures the way the GM writes them. Requiring a
    hand-authored manifest of library paths was prep-time work for a play-time
    problem."""
    client, fake, raw_token = sync_client
    client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token))
    r = _command(client, raw_token, "add-creature-0001", "add_creatures",
                 {"creatures": [{"name": "Desmohund", "count": 2}]}, 0)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["result"]["added"] == ["Desmohund"]


def test_an_unresolvable_creature_is_reported_not_guessed(sync_client):
    """Adding the wrong monster mid-round is worse than being asked which one."""
    client, fake, raw_token = sync_client
    client.get("/api/integrations/obsidian/v1/state", headers=headers(raw_token))
    r = _command(client, raw_token, "add-creature-0002", "add_creatures",
                 {"creatures": [{"name": "Hound"}]}, 0)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()["result"]
    assert body["added"] == []
    assert body["unresolved"][0]["name"] == "Hound"


# --------------------------------------------------------------------------
# PE-14c: SQL token storage in ``sql`` mode (Decision 1, Option A)

import json
import logging
import os
import uuid as _uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import event, select, update
from sqlalchemy.pool import StaticPool

from core.persistence import Campaign, Database, IntegrationToken, User, runtime
from core.persistence import integration_tokens

OTHER = "d" * 32
API = "/api/integrations/obsidian/v1"
XHR = {"X-Requested-With": "XMLHttpRequest"}


class _Spy:
    """Records campaign_lock and transaction nesting; flags any overlap."""

    def __init__(self):
        self.events, self.violations = [], []
        self.lock_depth = self.txn_depth = 0

    def lock(self, real):
        spy = self

        class Held:
            def __init__(self, campaign_id):
                self.inner = real(campaign_id)

            def __enter__(self):
                if spy.txn_depth:
                    spy.violations.append("lock inside a transaction")
                spy.lock_depth += 1
                spy.events.append("lock")
                return self.inner.__enter__()

            def __exit__(self, *exc):
                spy.lock_depth -= 1
                spy.events.append("unlock")
                return self.inner.__exit__(*exc)
        return Held

    def transaction(self, real):
        @contextmanager
        def wrapped():
            if self.lock_depth:
                self.violations.append("transaction inside campaign_lock")
            self.txn_depth += 1
            self.events.append("begin")
            try:
                with real() as session:
                    yield session
            finally:
                self.txn_depth -= 1
                self.events.append("end")
        return wrapped


@pytest.fixture
def sql_sync(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CAMPAIGNS_DIR", str(tmp_path / "campaigns"))
    for cid in (CID, OTHER):
        storage.ensure_campaign_dirs(cid)
    db = Database("sqlite+pysqlite:///:memory:", engine_options={
        "connect_args": {"check_same_thread": False}, "poolclass": StaticPool})
    db.create_schema()
    now = datetime(2026, 11, 18, tzinfo=timezone.utc)
    with db.transaction() as session:
        session.add(User(id="gm", username="gm", normalized_username="gm", display_name="gm",
                         password_hash="h", created_at=now))
        session.flush()
        for cid in (CID, OTHER):
            session.add(Campaign(id=cid, slug=cid, name=cid, system="pf2e",
                                 created_by_user_id="gm", created_at=now))
    spy = _Spy()
    monkeypatch.setattr(db, "transaction", spy.transaction(db.transaction))
    monkeypatch.setattr(obsidian_sync, "campaign_lock", spy.lock(obsidian_sync.campaign_lock))
    monkeypatch.setattr(runtime, "database", lambda: db)
    monkeypatch.setenv("OWNERSHIP_BACKEND", "sql")
    fake = FakeAdapter()
    flask_app = Flask(__name__)
    flask_app.secret_key = "test"
    register_obsidian_sync(flask_app, fake.callbacks())
    with flask_app.test_client() as client:
        yield SimpleNamespace(db=db, client=client, fake=fake, spy=spy, monkeypatch=monkeypatch)
    assert spy.violations == []


def _state(env, raw):
    return env.client.get(API + "/state", headers=headers(raw)).status_code


def _mint(env, label="tests"):
    response = env.client.post(API + "/tokens", json={"label": label}, headers=XHR)
    return response


def _minted(env):
    response = _mint(env)
    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    return body["token"], body["record"]["id"]


def _revoke(env, token_id):
    return env.client.delete(API + "/tokens/" + token_id, headers=XHR).status_code


def _row(env, credential_id):
    with env.db.session() as session:
        found = session.execute(select(IntegrationToken.__table__).where(
            IntegrationToken.credential_id == credential_id)).first()
        return None if found is None else dict(found._mapping)


def _file(cid=CID):
    with open(obsidian_sync.tokens_file(cid), "rb") as handle:
        return handle.read()


def _file_records(cid=CID):
    if not os.path.exists(obsidian_sync.tokens_file(cid)):
        return {}
    return {r["id"]: r for r in json.loads(_file(cid))["tokens"]}


def _file_token(env):
    """A token that exists only in tokens.json (minted before SQL mode)."""

    env.monkeypatch.setenv("OWNERSHIP_BACKEND", "json")
    raw, record = obsidian_sync.create_token(CID, "legacy")
    env.monkeypatch.setenv("OWNERSHIP_BACKEND", "sql")
    return raw, record["id"]


def _boom(*args, **kwargs):
    raise RuntimeError("injected failure")


def test_sql_existing_file_token_imports_and_keeps_authenticating(sql_sync):
    raw, token_id = _file_token(sql_sync)
    assert _row(sql_sync, token_id) is None
    assert _state(sql_sync, raw) == 200
    row = _row(sql_sync, token_id)
    assert row["campaign_id"] == CID and row["scope"] == ["@live"]
    assert row["minted_by_user_id"] is None and row["revoked_at"] is None
    assert _state(sql_sync, raw) == 200


def test_sql_mint_writes_sql_then_file_and_authenticates(sql_sync):
    raw, token_id = _minted(sql_sync)
    assert _row(sql_sync, token_id)["scope"] == ["@live"]
    assert token_id in _file_records()
    assert _state(sql_sync, raw) == 200
    assert _state(sql_sync, raw[:-1] + ("x" if raw[-1] != "x" else "y")) == 401


def test_sql_revocation_is_permanent_and_visible_in_a_new_transaction(sql_sync):
    raw, token_id = _minted(sql_sync)
    assert _revoke(sql_sync, token_id) == 200
    assert _row(sql_sync, token_id)["revoked_at"] is not None
    assert _file_records()[token_id]["revoked_at"]
    assert _state(sql_sync, raw) == 401
    assert _revoke(sql_sync, token_id) == 200  # retry-safe
    assert _state(sql_sync, raw) == 401


def test_sql_file_token_never_used_is_listed_and_revocable(sql_sync):
    raw, token_id = _file_token(sql_sync)
    assert token_id in [r["id"] for r in obsidian_sync.list_tokens(CID)]
    assert _revoke(sql_sync, token_id) == 200
    assert _row(sql_sync, token_id)["revoked_at"] is not None
    assert _state(sql_sync, raw) == 401


def test_sql_revoke_unimported_file_token_inserts_revoked_row(sql_sync):
    raw, token_id = _file_token(sql_sync)
    assert obsidian_sync.revoke_token(CID, token_id) is True
    row = _row(sql_sync, token_id)
    assert row is not None and row["revoked_at"] is not None and row["scope"] == ["@live"]
    assert _state(sql_sync, raw) == 401


def test_sql_revoke_crash_after_tombstone_refuses_on_restart(sql_sync):
    raw, token_id = _minted(sql_sync)
    with sql_sync.monkeypatch.context() as patch:
        patch.setattr(integration_tokens, "revoke_row", _boom)
        assert _revoke(sql_sync, token_id) == 503
    assert _file_records()[token_id]["revoked_at"]
    assert _row(sql_sync, token_id)["revoked_at"] is None  # the SQL step never ran
    # "Restart": nothing is cached in memory, so a fresh request is a fresh process view.
    assert _state(sql_sync, raw) == 401
    assert _row(sql_sync, token_id)["revoked_at"] is not None  # the import completed it
    sql_sync.monkeypatch.setenv("OWNERSHIP_BACKEND", "json")
    assert _state(sql_sync, raw) == 401


def test_sql_revoke_file_write_failure_changes_nothing(sql_sync):
    raw, token_id = _minted(sql_sync)
    before = _file()

    def refuse(*args, **kwargs):
        raise OSError("disk full")

    with sql_sync.monkeypatch.context() as patch:
        patch.setattr(storage, "atomic_write_json", refuse)
        assert _revoke(sql_sync, token_id) == 503
    assert _file() == before
    assert _row(sql_sync, token_id)["revoked_at"] is None
    assert _state(sql_sync, raw) == 200


def test_sql_revoke_retry_after_partial_is_idempotent(sql_sync):
    raw, token_id = _minted(sql_sync)
    with sql_sync.monkeypatch.context() as patch:
        patch.setattr(integration_tokens, "revoke_row", _boom)
        assert _revoke(sql_sync, token_id) == 503
    stamp = _file_records()[token_id]["revoked_at"]
    assert _revoke(sql_sync, token_id) == 200
    revoked_at = _row(sql_sync, token_id)["revoked_at"]
    assert revoked_at is not None
    assert _revoke(sql_sync, token_id) == 200
    assert _file_records()[token_id]["revoked_at"] == stamp
    assert _row(sql_sync, token_id)["revoked_at"] == revoked_at
    assert _state(sql_sync, raw) == 401


def test_revoke_store_failure_returns_503(sql_sync):
    raw, token_id = _minted(sql_sync)
    before = _file()
    with sql_sync.monkeypatch.context() as patch:
        patch.setattr(integration_tokens, "get_row", _boom)
        assert _revoke(sql_sync, token_id) == 503
    assert _file() == before
    assert _state(sql_sync, raw) == 200


def _flip_to_json(env):
    env.monkeypatch.setenv("OWNERSHIP_BACKEND", "json")


@pytest.mark.parametrize("stop", ["completed", "sql_step_raises", "mint_append_missing"])
def test_direct_json_flip_never_revives_revoked_token(sql_sync, stop):
    if stop == "mint_append_missing":
        with sql_sync.monkeypatch.context() as patch:
            patch.setattr(obsidian_sync, "_write_tokens", _boom)
            raw, token_id = _minted(sql_sync)
        assert token_id not in _file_records()
        assert _revoke(sql_sync, token_id) == 200
    else:
        raw, token_id = _minted(sql_sync)
        with sql_sync.monkeypatch.context() as patch:
            if stop == "sql_step_raises":
                patch.setattr(integration_tokens, "revoke_row", _boom)
            assert _revoke(sql_sync, token_id) == (200 if stop == "completed" else 503)
    _flip_to_json(sql_sync)
    assert _state(sql_sync, raw) == 401


def test_revoke_between_mint_commit_and_file_append(sql_sync):
    real = integration_tokens.insert_minted
    seen = {}

    def insert_then_revoke(campaign_id, record, **kwargs):
        real(campaign_id, record, **kwargs)
        seen["revoked"] = obsidian_sync.revoke_token(campaign_id, record["id"])
        seen["file"] = dict(_file_records()[record["id"]])

    sql_sync.monkeypatch.setattr(integration_tokens, "insert_minted", insert_then_revoke)
    raw, token_id = _minted(sql_sync)
    assert seen["revoked"] is True and seen["file"]["revoked_at"]
    assert _file_records()[token_id] == seen["file"]  # mint's append left it unchanged
    assert list(json.loads(_file())["tokens"]).count(seen["file"]) == 1
    assert _row(sql_sync, token_id)["revoked_at"] is not None
    assert _state(sql_sync, raw) == 401
    _flip_to_json(sql_sync)
    assert _state(sql_sync, raw) == 401


def test_revoke_unknown_id_before_mint_commit_writes_nothing(sql_sync):
    fixed = _uuid.UUID("0123456789ab" + "0" * 20)
    before = _file() if os.path.exists(obsidian_sync.tokens_file(CID)) else None
    assert _revoke(sql_sync, fixed.hex[:12]) == 404
    after = _file() if os.path.exists(obsidian_sync.tokens_file(CID)) else None
    assert after == before
    sql_sync.monkeypatch.setattr(obsidian_sync.uuid, "uuid4", lambda: fixed)
    raw, token_id = _minted(sql_sync)
    assert token_id == fixed.hex[:12]
    assert _row(sql_sync, token_id)["revoked_at"] is None
    assert _state(sql_sync, raw) == 200


def test_sql_bearer_refused_when_import_fails_after_partial_revoke(sql_sync, caplog):
    raw, token_id = _minted(sql_sync)
    with sql_sync.monkeypatch.context() as patch:
        patch.setattr(integration_tokens, "revoke_row", _boom)
        assert _revoke(sql_sync, token_id) == 503
    sql_sync.monkeypatch.setattr(integration_tokens, "import_file_records", _boom)
    with caplog.at_level(logging.WARNING):
        # The file tombstone refuses it even though the import fails.
        assert _state(sql_sync, raw) == 401
        other_raw, _other = _minted(sql_sync)
        assert _state(sql_sync, other_raw) == 200
    assert _row(sql_sync, token_id)["revoked_at"] is None
    logged = [r.getMessage() for r in caplog.records if "obsidian_token_import_failed" in
              r.getMessage()]
    assert logged and all(raw not in m and other_raw not in m for m in logged)


def test_sql_bearer_file_read_failure_is_500_not_accepted(sql_sync):
    raw, _token_id = _minted(sql_sync)

    def unreadable(path):
        raise OSError("io error")

    sql_sync.monkeypatch.setattr(storage, "load_json_strict", unreadable)
    assert _state(sql_sync, raw) == 500


@pytest.mark.parametrize("content", [b"{not json", b"[1, 2]", b'{"tokens": {"a": 1}}'])
def test_sql_corrupt_tokens_file_refuses_revoke_and_repair(sql_sync, caplog, content):
    raw, token_id = _minted(sql_sync)
    with open(obsidian_sync.tokens_file(CID), "wb") as handle:
        handle.write(content)
    assert _revoke(sql_sync, token_id) == 503
    with caplog.at_level(logging.WARNING):
        listed = obsidian_sync.list_tokens(CID)
    assert [r["id"] for r in listed] == [token_id]
    assert any("obsidian_token_repair_failed" in r.getMessage() for r in caplog.records)
    assert _file() == content
    assert _row(sql_sync, token_id)["revoked_at"] is None
    assert _state(sql_sync, raw) == 500


def test_sql_missing_tokens_file_is_empty(sql_sync):
    path = obsidian_sync.tokens_file(CID)
    if os.path.exists(path):
        os.remove(path)
    assert obsidian_sync.list_tokens(CID) == []
    assert not os.path.exists(path)
    raw, token_id = _minted(sql_sync)
    assert token_id in _file_records()
    assert _state(sql_sync, raw) == 200


def test_revoke_other_campaign_credential_is_not_found(sql_sync):
    raw_b, record_b = obsidian_sync.create_token(OTHER, "other")
    before_b, row_before = _file(OTHER), _row(sql_sync, record_b["id"])
    before_a = _file() if os.path.exists(obsidian_sync.tokens_file(CID)) else None
    assert _revoke(sql_sync, record_b["id"]) == 404  # the route's campaign is CID
    assert obsidian_sync.revoke_token(CID, record_b["id"]) is False
    assert _file(OTHER) == before_b and _row(sql_sync, record_b["id"]) == row_before
    after_a = _file() if os.path.exists(obsidian_sync.tokens_file(CID)) else None
    assert after_a == before_a


def test_import_skips_id_held_by_other_campaign(sql_sync, caplog):
    _raw_b, record_b = obsidian_sync.create_token(OTHER, "other")
    row_before = _row(sql_sync, record_b["id"])
    forged = {"id": record_b["id"], "label": "forged", "token_hash": "f" * 64,
              "last_four": "ffff", "created_at": "2026-11-18T00:00:00Z",
              "last_used_at": None, "revoked_at": "2026-11-18T00:00:00Z"}
    with caplog.at_level(logging.WARNING):
        assert integration_tokens.import_file_records(CID, [forged]) == 0
    assert _row(sql_sync, record_b["id"]) == row_before
    messages = [r.getMessage() for r in caplog.records]
    assert any(m == f"obsidian_token_import_conflict campaign={CID}" for m in messages)


def test_sql_last_used_at_updates_without_stream_or_file(sql_sync):
    from core.persistence import streams

    raw, token_id = _minted(sql_sync)
    before = _file()
    sql_sync.monkeypatch.setattr(streams, "lock_stream", _boom)
    sql_sync.spy.events.clear()
    assert _state(sql_sync, raw) == 200
    first = _row(sql_sync, token_id)["last_used_at"]
    assert first is not None
    assert _state(sql_sync, raw) == 200
    assert _row(sql_sync, token_id)["last_used_at"] == first  # within 300 s: no write
    with sql_sync.db.session() as session:  # age it past the granularity
        session.execute(update(IntegrationToken).where(
            IntegrationToken.credential_id == token_id).values(
            last_used_at=datetime.now(timezone.utc) - timedelta(seconds=301)))
        session.commit()
    assert _state(sql_sync, raw) == 200
    assert _row(sql_sync, token_id)["last_used_at"] != first
    assert _file() == before


def test_sql_mint_mirror_failure_still_returns_token(sql_sync, caplog):
    def refuse(*args, **kwargs):
        raise OSError("disk full")

    with caplog.at_level(logging.WARNING), sql_sync.monkeypatch.context() as patch:
        patch.setattr(storage, "atomic_write_json", refuse)
        response = _mint(sql_sync)
    assert response.status_code == 201
    raw = response.get_json()["token"]
    messages = [r.getMessage() for r in caplog.records]
    assert f"obsidian_token_mirror_failed campaign={CID}" in messages
    assert all(raw not in m for m in messages)
    assert _state(sql_sync, raw) == 200


def test_mint_sql_failure_writes_no_file(sql_sync):
    before = _file() if os.path.exists(obsidian_sync.tokens_file(CID)) else None
    sql_sync.monkeypatch.setattr(integration_tokens, "insert_minted", _boom)
    response = _mint(sql_sync)
    assert response.status_code == 503
    assert "token" not in response.get_json()
    after = _file() if os.path.exists(obsidian_sync.tokens_file(CID)) else None
    assert after == before


def test_list_tokens_repairs_missing_mirror_after_restart(sql_sync):
    with sql_sync.monkeypatch.context() as patch:
        patch.setattr(obsidian_sync, "_write_tokens", _boom)
        _raw, token_id = _minted(sql_sync)
    _legacy_raw, legacy_id = _file_token(sql_sync)
    obsidian_sync.revoke_token(CID, legacy_id)
    doc = json.loads(_file())
    doc["tokens"].append({"id": "fileonly0001", "label": "x", "token_hash": "e" * 64,
                          "last_four": "eeee", "created_at": "2026-11-18T00:00:00Z",
                          "last_used_at": None, "revoked_at": "2026-11-18T00:00:00Z"})
    storage.atomic_write_json(obsidian_sync.tokens_file(CID), doc)
    assert token_id not in _file_records()
    listed = {r["id"] for r in obsidian_sync.list_tokens(CID)}
    records = _file_records()
    assert token_id in records and not records[token_id]["revoked_at"]
    assert records[legacy_id]["revoked_at"] and records["fileonly0001"]["revoked_at"]
    assert {token_id, legacy_id, "fileonly0001"} <= listed


def test_mint_mirror_and_repair_never_duplicate_id(sql_sync):
    _minted(sql_sync)
    _minted(sql_sync)
    for _ in range(2):
        obsidian_sync.list_tokens(CID)
    ids = [r["id"] for r in json.loads(_file())["tokens"]]
    assert len(ids) == len(set(ids)) == 2


def test_list_tokens_completes_partial_revoke_in_sql(sql_sync):
    raw, token_id = _minted(sql_sync)
    with sql_sync.monkeypatch.context() as patch:
        patch.setattr(integration_tokens, "revoke_row", _boom)
        assert _revoke(sql_sync, token_id) == 503
    assert _row(sql_sync, token_id)["revoked_at"] is None
    listed = {r["id"]: r for r in obsidian_sync.list_tokens(CID)}
    assert _row(sql_sync, token_id)["revoked_at"] is not None
    assert listed[token_id]["revoked_at"]


@pytest.mark.parametrize("mode", ["json", "shadow"])
def test_json_and_shadow_modes_never_touch_sql_tokens(sql_sync, mode):
    sql_sync.monkeypatch.setenv("OWNERSHIP_BACKEND", mode)
    for name in ("list_rows", "get_row", "find_by_digest", "import_file_records",
                 "insert_minted", "revoke_row", "touch_last_used", "repair_token_mirror"):
        sql_sync.monkeypatch.setattr(integration_tokens, name, _boom)
    sql_sync.monkeypatch.setattr(runtime, "database", _boom)
    raw, token_id = _minted(sql_sync)
    assert _state(sql_sync, raw) == 200
    assert [r["id"] for r in obsidian_sync.list_tokens(CID)] == [token_id]
    assert _revoke(sql_sync, token_id) == 200
    assert _state(sql_sync, raw) == 401


def test_lock_order_import_revoke_mint_and_repair(sql_sync):
    spy = sql_sync.spy
    raw, token_id = _file_token(sql_sync)
    spy.events.clear()
    assert _state(sql_sync, raw) == 200  # lazy import
    first_lock = spy.events.index("lock")
    assert "begin" in spy.events[spy.events.index("unlock"):]
    assert "begin" not in spy.events[first_lock:spy.events.index("unlock")]
    spy.events.clear()
    assert _revoke(sql_sync, token_id) == 200
    # Step 0's read opens and closes before the lock; the write follows the unlock.
    assert spy.events[:2] == ["begin", "end"]
    assert spy.events[2:4] == ["lock", "unlock"]
    assert spy.events[4:] == ["begin", "end"]
    spy.events.clear()
    _minted(sql_sync)
    assert spy.events[:2] == ["begin", "end"] and spy.events[2:4] == ["lock", "unlock"]
    spy.events.clear()
    obsidian_sync.list_tokens(CID)
    assert spy.events[:2] == ["begin", "end"] and spy.events[2:4] == ["lock", "unlock"]
    assert spy.violations == []


# --------------------------------------------------------------------------
# Implementation review 1: R1-1 (unreadable vs missing), R1-5 (insert races),
# R1-6 (the last-use cutoff lives in the UPDATE)


def _metadata_denied(env):
    """A token file whose lookup fails: ``exists`` answers False, the open is denied."""

    def denied(path):
        raise PermissionError(13, "Permission denied", path)

    env.monkeypatch.setattr(obsidian_sync.os.path, "exists", lambda path: False)
    env.monkeypatch.setattr(storage, "load_json_strict", denied)


def test_sql_metadata_denied_token_file_is_unavailable_not_empty(sql_sync):
    raw, token_id = _minted(sql_sync)
    with sql_sync.monkeypatch.context() as patch:  # file stamped, SQL step lost
        patch.setattr(integration_tokens, "revoke_row", _boom)
        assert _revoke(sql_sync, token_id) == 503
    assert _row(sql_sync, token_id)["revoked_at"] is None
    before = _file()
    _metadata_denied(sql_sync)
    # The SQL row is still live; reading the file as empty would accept it.
    assert _state(sql_sync, raw) == 500
    assert _revoke(sql_sync, token_id) == 503
    assert _file() == before
    assert _row(sql_sync, token_id)["revoked_at"] is None


def test_sql_genuinely_missing_file_is_empty_and_metadata_denied_is_not(sql_sync):
    raw, _token_id = _minted(sql_sync)
    os.remove(obsidian_sync.tokens_file(CID))
    assert _state(sql_sync, raw) == 200  # absence: SQL decides
    _metadata_denied(sql_sync)
    assert _state(sql_sync, raw) == 500
    with pytest.raises(obsidian_sync.TokenStoreUnavailable):
        obsidian_sync._load_tokens_strict(CID)


def _file_record(token_id, *, revoked=False, digest=None):
    return {"id": token_id, "label": "file", "token_hash": digest or _uuid.uuid4().hex * 2,
            "last_four": "abcd", "created_at": "2026-11-18T00:00:00Z", "last_used_at": None,
            "revoked_at": "2026-11-18T01:00:00Z" if revoked else None}


def _first_call_misses(env, name, empty):
    """Make the first call of ``name`` miss a row another writer commits just after it."""

    real = getattr(integration_tokens, name)
    calls = []

    def stale(*args, **kwargs):
        calls.append(1)
        return empty() if len(calls) == 1 else real(*args, **kwargs)

    env.monkeypatch.setattr(integration_tokens, name, stale)


def test_import_collision_with_another_campaign_commits_the_other_records(sql_sync, caplog):
    _raw_b, record_b = obsidian_sync.create_token(OTHER, "other")
    row_b = _row(sql_sync, record_b["id"])
    _first_call_misses(sql_sync, "_foreign", set)
    batch = [_file_record(record_b["id"], revoked=True), _file_record("fresh1")]
    with caplog.at_level(logging.WARNING):
        assert integration_tokens.import_file_records(CID, batch) == 1
    assert _row(sql_sync, record_b["id"]) == row_b  # never moved or revoked
    assert _row(sql_sync, "fresh1")["campaign_id"] == CID
    assert any(r.getMessage() == f"obsidian_token_import_conflict campaign={CID}"
               for r in caplog.records)


def test_import_overlapping_same_campaign_insert_keeps_its_revocation(sql_sync):
    _raw, token_id = _minted(sql_sync)
    _first_call_misses(sql_sync, "_held", dict)
    # Another writer inserted the live row after this import read; the insert
    # loses, and the file's revocation is still copied onto the winning row.
    batch = [_file_record(token_id, revoked=True), _file_record("fresh2")]
    assert integration_tokens.import_file_records(CID, batch) == 2
    assert _row(sql_sync, token_id)["revoked_at"] is not None
    assert _row(sql_sync, "fresh2")["revoked_at"] is None


@pytest.mark.parametrize("order", [(False, True), (True, False)])
def test_import_repeated_id_in_one_snapshot_ends_revoked(sql_sync, order):
    digest = _uuid.uuid4().hex * 2
    batch = [_file_record("twice", revoked=r, digest=digest) for r in order]
    integration_tokens.import_file_records(CID, batch)
    assert _row(sql_sync, "twice")["revoked_at"] is not None


def test_import_reads_are_scoped_and_read_only_ids_of_other_campaigns(sql_sync):
    _raw_b, record_b = obsidian_sync.create_token(OTHER, "other")
    seen = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "integration_tokens" in statement:
            seen.append(statement)

    engine = sql_sync.db.engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        integration_tokens.import_file_records(CID, [_file_record(record_b["id"])])
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert seen
    for statement in seen:
        columns, where = statement.split("FROM", 1)[0], statement.split("WHERE", 1)[1]
        assert "campaign_id" in where
        if "!=" in where or "<>" in where:  # the collision probe: ids only
            assert "token_digest" not in columns and "label" not in columns


def test_file_only_revoke_collides_with_another_campaign_without_error(sql_sync, caplog):
    _raw_b, record_b = obsidian_sync.create_token(OTHER, "other")
    row_b = _row(sql_sync, record_b["id"])
    with caplog.at_level(logging.WARNING):
        integration_tokens.revoke_row(CID, record_b["id"],
                                      _file_record(record_b["id"], revoked=True))
    assert _row(sql_sync, record_b["id"]) == row_b
    assert any("obsidian_token_import_conflict" in r.getMessage() for r in caplog.records)


def test_file_only_revoke_overlapping_insert_revokes_the_winning_row(sql_sync):
    _raw, token_id = _minted(sql_sync)
    # Both of revoke_row's reads miss the row; its conflict-safe insert loses
    # to it and the reconcile revokes it instead of raising.
    _first_call_misses(sql_sync, "_revoke_live", bool)
    _first_call_misses(sql_sync, "_held", dict)
    integration_tokens.revoke_row(CID, token_id, _file_record(token_id, revoked=True))
    assert _row(sql_sync, token_id)["revoked_at"] is not None


def test_last_used_cutoff_is_enforced_against_the_stored_row(sql_sync):
    _raw, token_id = _minted(sql_sync)
    first = integration_tokens.touch_last_used(CID, token_id)
    assert first is not None
    # A second request that observed the same old value must not write again.
    assert integration_tokens.touch_last_used(CID, token_id) is None
    stored = _row(sql_sync, token_id)["last_used_at"]
    assert stored.replace(tzinfo=timezone.utc) == first
    with sql_sync.db.session() as session:
        session.execute(update(IntegrationToken).where(
            IntegrationToken.credential_id == token_id).values(
            last_used_at=datetime.now(timezone.utc) - timedelta(seconds=301)))
        session.commit()
    assert integration_tokens.touch_last_used(CID, token_id) is not None
    assert integration_tokens.touch_last_used(CID, token_id) is None


def test_last_used_reports_no_write_for_a_missing_or_foreign_row(sql_sync):
    _raw_b, record_b = obsidian_sync.create_token(OTHER, "other")
    assert integration_tokens.touch_last_used(CID, "missing") is None
    assert integration_tokens.touch_last_used(CID, record_b["id"]) is None
    assert _row(sql_sync, record_b["id"])["last_used_at"] is None


def test_sql_deeply_nested_tokens_file_is_unavailable_not_500(sql_sync):
    raw, token_id = _minted(sql_sync)
    content = b"[" * 200000 + b"]" * 200000  # json.load raises RecursionError
    with open(obsidian_sync.tokens_file(CID), "wb") as handle:
        handle.write(content)
    assert _revoke(sql_sync, token_id) == 503
    assert _file() == content
    assert _state(sql_sync, raw) == 500
