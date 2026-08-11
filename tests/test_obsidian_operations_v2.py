from __future__ import annotations

import json
from types import SimpleNamespace

import app as app_module
from services import session_ops_rolls


def _enemy():
    return SimpleNamespace(
        instance_id="enemy-1",
        name="Test Enemy",
        is_pc=False,
        initiative=0,
        perception=9,
        fort=8,
        ref=7,
        will=6,
        spell_attack=10,
        skills=[{"name": "Stealth", "total": 11}],
        strikes=[{"name": "Claw", "bonus": 12, "damage": "2d6+4 slashing"}],
        spellcasting=[],
        persistent_damage="",
        conditions={},
    )


def test_obsidian_attack_roll_uses_server_actor_modifier(monkeypatch):
    enemy = _enemy()
    monkeypatch.setattr(app_module, "ACTIVE_ENCOUNTER", [enemy])
    monkeypatch.setattr(app_module, "PARTY_LIBRARY", {})
    monkeypatch.setattr(app_module, "GM_SECRET_LOG", [])
    monkeypatch.setattr(app_module, "sse_broadcast", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(session_ops_rolls.random, "randint", lambda _low, _high: 10)

    result = app_module._obsidian_resolve_roll({
        "target_id": "enemy-1",
        "roll_kind": "attack",
        "strike_name": "Claw",
        "map_stage": 1,
        "visibility": "gm",
    })

    assert result["modifier"] == 7
    assert result["total"] == 17
    assert result["visibility"] == "gm"
    assert app_module.GM_SECRET_LOG[-1]["total"] == 17


def test_agile_enemy_uses_four_and_eight_map_penalties(monkeypatch):
    enemy = _enemy()
    enemy.strikes[0]["traits"] = ["agile"]
    monkeypatch.setattr(app_module, "ACTIVE_ENCOUNTER", [enemy])
    monkeypatch.setattr(app_module, "PARTY_LIBRARY", {})
    monkeypatch.setattr(app_module, "GM_SECRET_LOG", [])
    monkeypatch.setattr(app_module, "sse_broadcast", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(session_ops_rolls.random, "randint", lambda _low, _high: 10)

    result = app_module._obsidian_resolve_roll({
        "target_id": "enemy-1", "roll_kind": "attack",
        "strike_name": "Claw", "map_stage": 2, "visibility": "gm",
    })

    assert result["modifier"] == 4


def test_gm_only_combat_log_entries_are_absent_from_player_history(monkeypatch):
    entries = [
        {"msg": "Secret enemy roll", "gm_only": True},
        {"msg": "Public player roll", "gm_only": False},
    ]
    monkeypatch.setattr(app_module, "_is_gm", lambda: False)
    monkeypatch.setattr(app_module, "_hidden_npc_names", lambda: [])

    assert app_module._scrub_log_entries_for_players(entries) == [entries[1]]


def test_room_reminders_replace_by_stable_room_id(monkeypatch):
    monkeypatch.setattr(app_module, "ROUND_EVENTS", [])
    monkeypatch.setattr(app_module, "_persist_encounter_state", lambda: None)
    monkeypatch.setattr(app_module, "_broadcast_encounter_state", lambda: None)
    reminders = [{
        "id": "reinforcement",
        "round": 2,
        "title": "Reinforcements",
        "text": "The south door opens.",
        "show_on_table": False,
    }]

    first = app_module._obsidian_sync_room_reminders("room-c10", reminders)
    second = app_module._obsidian_sync_room_reminders("room-c10", reminders)

    assert first["reminder_count"] == 1
    assert second["reminder_count"] == 1
    assert len(app_module.ROUND_EVENTS) == 1
    assert app_module.ROUND_EVENTS[0]["id"] == "room:room-c10:reinforcement"


def test_room_launch_requires_valid_saved_stage(monkeypatch, tmp_path):
    enemy = _enemy()
    enemy.file_path = "test-enemy.json"
    monkeypatch.setattr(app_module, "ENCOUNTER_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "MONSTER_LIBRARY", {"test-enemy.json": enemy})
    monkeypatch.setattr(app_module, "PARTY_LIBRARY", {})
    monkeypatch.setattr(app_module, "ACTIVE_ENCOUNTER", [])
    monkeypatch.setattr(app_module, "_RECENT_DEFEATED", [])
    monkeypatch.setattr(app_module, "_persist_encounter_state", lambda: None)
    monkeypatch.setattr(app_module, "_broadcast_encounter_state", lambda: None)
    monkeypatch.setattr(app_module, "_combat_log", lambda *_args, **_kwargs: None)
    (tmp_path / "C10 Stage.json").write_text(json.dumps({
        "format": "stage",
        "monsters": [{"path": "test-enemy.json", "count": 2}],
    }), encoding="utf-8")

    result = app_module._obsidian_launch_room_encounter({
        "area_id": "room-c10",
        "encounter_template": "C10 Stage",
        "add_party": False,
    })

    assert result["combatant_count"] == 2
    assert [actor.name for actor in app_module.ACTIVE_ENCOUNTER] == ["Test Enemy 1", "Test Enemy 2"]
