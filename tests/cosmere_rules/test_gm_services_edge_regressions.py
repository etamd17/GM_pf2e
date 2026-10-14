"""Focused regressions for GM-service persistence and actor projections."""
import json

import pytest

from systems.cosmere.actor import CosmereActor
from systems.cosmere.build import CosmereBuild
from systems.cosmere.custom_adversaries import (
    adversary_to_actor_document,
    create_adversary,
)
from systems.cosmere.rules.schema import to_dict


def _binding():
    return to_dict(CosmereBuild().rules_binding)


def _action_with_independent_consumption_bounds():
    return {
        "id": "variable-focus", "source_id": "source.variable-focus",
        "item_id": "item.variable-focus", "actor_id": None,
        "name": "Variable Focus", "kind": "action",
        "activation": {
            "type": "utility", "cost": {"type": "action", "value": 1},
            "consume": [{"type": "resource", "resource": "focus",
                         "amount": 2, "minimum": 1, "maximum": 4}],
            "flavor": "",
        },
        "test": {"type": "utility", "skill": None, "attribute": None,
                 "modifier": ""},
        "damage": {"formula": None, "type": None, "skill": None,
                   "attribute": None, "graze_formula": None},
        "target": {}, "plot_die": False, "opportunity": None,
        "complication": None, "use_limit": None, "description": "",
        "allow_multiple": False, "permissions": {},
    }


def _adversary_payload():
    return {
        "name": "Edge Rival", "role": "ordinary", "world": "roshar",
        "era": None, "level": 2,
        "defenses": {"physical": 13, "cognitive": 12, "spiritual": 11},
        "resources": {"health": {"current": 18, "max": 18}},
        "traits": [], "equipment": [],
        "actions": [_action_with_independent_consumption_bounds()],
    }


@pytest.fixture
def custom_adversary_delete_api(tmp_path, monkeypatch):
    import app as host

    state, created = create_adversary(
        {}, _adversary_payload(), binding=_binding(),
        expected_store_revision=0, request_id="request.edge.setup",
        id_factory=lambda: "ab" * 16)
    store_path = tmp_path / "adversaries.json"
    store_path.write_text(json.dumps(state), encoding="utf-8")
    stage_path = tmp_path / "stage.json"

    monkeypatch.setattr(host, "COSMERE_ADVERSARIES_FILE", str(store_path))
    monkeypatch.setattr(host, "_cosmere_encounter_stage_path",
                        lambda: str(stage_path))
    monkeypatch.setattr(host, "ENCOUNTER_DIR", str(tmp_path / "encounters"))
    monkeypatch.setattr(host, "ACTIVE_ENCOUNTER", [])
    monkeypatch.setattr(host, "_is_gm", lambda: True)
    monkeypatch.setattr(host, "_active_system", lambda: "cosmere")
    monkeypatch.setattr(host, "_load_campaign_config", lambda: {})
    return host.app.test_client(), store_path, stage_path, created["adversary"]["id"]


def _configure_invalid_stage(stage_path, case):
    if case == "corrupt_json":
        stage_path.write_text("{not-json", encoding="utf-8")
    elif case == "non_object":
        stage_path.write_text("[]", encoding="utf-8")
    elif case == "missing_envelope":
        stage_path.write_text("{}", encoding="utf-8")
    elif case == "adversaries_not_list":
        stage_path.write_text(json.dumps({
            "schema_version": 1, "revision": 1, "receipts": [],
            "stage": {"adversaries": {"id": "ab" * 16}},
        }), encoding="utf-8")
    elif case == "adversary_without_identity":
        stage_path.write_text(json.dumps({
            "schema_version": 1, "revision": 1, "receipts": [],
            "stage": {"adversaries": [{"name": "Unidentified"}]},
        }), encoding="utf-8")
    elif case == "io_error":
        stage_path.mkdir()
    else:  # pragma: no cover - protects the test table itself
        raise AssertionError(case)


@pytest.mark.parametrize("case", [
    "corrupt_json",
    "non_object",
    "missing_envelope",
    "adversaries_not_list",
    "adversary_without_identity",
    "io_error",
])
def test_custom_adversary_delete_fails_closed_when_stage_references_are_unreadable(
        custom_adversary_delete_api, case):
    client, store_path, stage_path, adversary_id = custom_adversary_delete_api
    _configure_invalid_stage(stage_path, case)

    response = client.delete(
        f"/api/cosmere/adversaries/custom/{adversary_id}", json={
            "expected_revision": 0,
            "request_id": f"request.edge.delete.{case}",
        })

    assert response.status_code == 400
    assert "staged encounter reference" in response.get_json()["error"].lower()
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert [row["id"] for row in stored["adversaries"]] == [adversary_id]


def test_custom_adversary_delete_allows_missing_stage_state(
        custom_adversary_delete_api):
    client, store_path, stage_path, adversary_id = custom_adversary_delete_api
    assert not stage_path.exists()

    response = client.delete(
        f"/api/cosmere/adversaries/custom/{adversary_id}", json={
            "expected_revision": 0,
            "request_id": "request.edge.delete.missing-stage",
        })

    assert response.status_code == 200, response.get_json()
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert stored["adversaries"] == []


def test_actor_projection_preserves_independent_consumption_amount_and_bounds():
    _state, created = create_adversary(
        {}, _adversary_payload(), binding=_binding(),
        expected_store_revision=0, request_id="request.edge.projection",
        id_factory=lambda: "cd" * 16)

    document = adversary_to_actor_document(created["adversary"])
    projected = document["items"][-1]["system"]["activation"]["consume"][0]
    assert projected["value"] == {"actual": 2, "min": 1, "max": 4}

    actor = CosmereActor(document)
    assert actor.actions[0]["activation"]["consume"] == [{
        "type": "resource", "resource": "focus",
        "amount": 2, "minimum": 1, "maximum": 4,
    }]
