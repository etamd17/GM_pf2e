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
