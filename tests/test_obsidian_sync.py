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
