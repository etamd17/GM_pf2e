"""PF2e condition recovery across overnight-rest entry points."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import app


_FIXTURE = Path(__file__).parent / "fixtures" / "kyle_l10.json"


@pytest.fixture
def rested_character(tmp_path, monkeypatch):
    """Create one damaged PC with Wounded, Doomed, and Drained at value 2."""

    def create(*, natural_healing_reaches_full):
        source = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        baseline_doc = copy.deepcopy(source)
        baseline_build = baseline_doc.get("build", baseline_doc)
        baseline_build["conditions"] = {}
        baseline_build.pop("current_hp", None)
        baseline = app.Character(baseline_doc)

        build = source.get("build", source)
        build["conditions"] = {
            "wounded": 2,
            "doomed": 2,
            "drained": 2,
            "frightened": 2,
        }
        recovery = max(1, int(baseline.mods.get("con", 0))) * baseline.level
        post_rest_max = baseline.hp - baseline.level  # Drained 2 -> Drained 1
        if natural_healing_reaches_full:
            current_hp = post_rest_max - recovery + 5
        else:
            current_hp = post_rest_max - recovery - 5
        assert 0 < current_hp <= baseline.hp - (2 * baseline.level)
        build["current_hp"] = current_hp

        path = tmp_path / "Kyle.json"
        path.write_text(json.dumps(source), encoding="utf-8")
        actor = app.Character(source, file_path=str(path))
        monkeypatch.setattr(app, "PARTY_LIBRARY", {actor.name: actor})
        monkeypatch.setattr(app, "_PC_FILE_CACHE", {actor.name: path.name})
        monkeypatch.setattr(app, "_PC_PERSIST_DIRTY", set())
        monkeypatch.setattr(app, "ACTIVE_ENCOUNTER", [])
        monkeypatch.setattr(
            app,
            "get_pc_file_path",
            lambda name: str(path) if name == actor.name else None,
        )
        monkeypatch.setattr(app, "_is_gm", lambda: True)
        monkeypatch.setattr(app, "_broadcast_pc_state", lambda *_args: None)
        monkeypatch.setattr(app, "_broadcast_encounter_state", lambda: None)
        return {
            "name": actor.name,
            "path": path,
            "starting_hp": current_hp,
            "starting_max": actor.hp,
            "recovery": recovery,
            "post_rest_max": post_rest_max,
        }

    return create


def _stored_build(path):
    document = json.loads(path.read_text(encoding="utf-8"))
    return document.get("build", document)


@pytest.mark.parametrize("endpoint_kind", ["single", "party"])
@pytest.mark.parametrize("reaches_full", [False, True])
def test_full_night_rest_applies_official_condition_recovery(
    rested_character, endpoint_kind, reaches_full
):
    state = rested_character(natural_healing_reaches_full=reaches_full)
    client = app.app.test_client()
    if endpoint_kind == "single":
        response = client.post(f"/api/long_rest/{state['name']}", json={})
    else:
        response = client.post("/api/rest/apply", json={"type": "long"})

    assert response.status_code == 200
    stored = _stored_build(state["path"])
    reloaded = app.PARTY_LIBRARY[state["name"]]
    expected_hp = min(
        state["post_rest_max"],
        state["starting_hp"] + state["recovery"],
    )
    expected_wounded = 0 if reaches_full else 2

    assert stored["current_hp"] == expected_hp == reloaded.current_hp
    assert reloaded.hp == state["post_rest_max"]
    assert stored["conditions"]["drained"] == 1
    assert reloaded.conditions.get("drained") == 1
    assert stored["conditions"]["doomed"] == 1
    assert reloaded.conditions.get("doomed") == 1
    assert stored["conditions"]["wounded"] == expected_wounded
    assert reloaded.conditions.get("wounded", 0) == expected_wounded


@pytest.mark.parametrize("endpoint_kind", ["single", "party"])
@pytest.mark.parametrize("heal_full", [False, True])
def test_daily_preparations_only_clear_wounded_when_hp_is_restored_to_full(
    rested_character, endpoint_kind, heal_full
):
    state = rested_character(natural_healing_reaches_full=False)
    client = app.app.test_client()
    if endpoint_kind == "single":
        response = client.post(
            f"/api/daily_prep/{state['name']}",
            json={"heal_full": heal_full},
        )
    else:
        response = client.post(
            "/api/daily_prep_all",
            json={"heal_full": heal_full},
        )

    assert response.status_code == 200
    stored = _stored_build(state["path"])
    reloaded = app.PARTY_LIBRARY[state["name"]]
    expected_hp = state["starting_max"] if heal_full else state["starting_hp"]
    expected_wounded = 0 if heal_full else 2

    assert reloaded.hp == state["starting_max"]
    assert reloaded.current_hp == expected_hp
    if heal_full:
        assert "current_hp" not in stored
    else:
        assert stored["current_hp"] == expected_hp
    assert stored["conditions"]["drained"] == 2
    assert reloaded.conditions.get("drained") == 2
    assert stored["conditions"]["doomed"] == 2
    assert reloaded.conditions.get("doomed") == 2
    assert stored["conditions"]["wounded"] == expected_wounded
    assert reloaded.conditions.get("wounded", 0) == expected_wounded


@pytest.mark.parametrize("endpoint_kind", ["single", "party"])
def test_daily_preparations_after_long_rest_do_not_repeat_condition_recovery(
    rested_character, endpoint_kind
):
    state = rested_character(natural_healing_reaches_full=False)
    client = app.app.test_client()

    rest = client.post(f"/api/long_rest/{state['name']}", json={})
    assert rest.status_code == 200

    if endpoint_kind == "single":
        prep = client.post(
            f"/api/daily_prep/{state['name']}",
            json={"heal_full": False},
        )
    else:
        prep = client.post(
            "/api/daily_prep_all",
            json={"heal_full": False},
        )

    assert prep.status_code == 200
    stored = _stored_build(state["path"])
    reloaded = app.PARTY_LIBRARY[state["name"]]
    assert stored["conditions"]["drained"] == 1
    assert reloaded.conditions.get("drained") == 1
    assert stored["conditions"]["doomed"] == 1
    assert reloaded.conditions.get("doomed") == 1
    assert stored["conditions"]["wounded"] == 2
    assert reloaded.conditions.get("wounded") == 2
