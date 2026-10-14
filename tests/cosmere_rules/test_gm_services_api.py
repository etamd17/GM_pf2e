"""Flask and campaign-persistence adapters for neutral Cosmere GM services."""
from copy import deepcopy
import json
from types import SimpleNamespace
import uuid

import pytest

from systems.cosmere.build import CosmereBuild
from systems.cosmere.custom_adversaries import create_adversary
from systems.cosmere.rules.schema import to_dict


def base_build(name="API Hero"):
    return {
        "name": name, "level": 1, "path": "warrior", "ancestry": "Human",
        "attributes": {"str": 2, "spd": 2, "int": 2, "wil": 2,
                       "awa": 2, "pre": 2},
        "skills": {"ath": 1, "agi": 1, "hwp": 1},
        "talents": [{"id": "talent.test.brace", "name": "Brace"}],
        "expertises": [], "epic_choices": [],
    }


def action():
    return {
        "id": "test-action", "source_id": "source.test-action",
        "item_id": "item.test-action", "actor_id": None,
        "name": "Test Action", "kind": "action",
        "activation": {"type": "utility",
                       "cost": {"type": "action", "value": 1},
                       "consume": [], "flavor": ""},
        "test": {"type": "utility", "skill": None, "attribute": None,
                 "modifier": ""},
        "damage": {"formula": None, "type": None, "skill": None,
                   "attribute": None, "graze_formula": None},
        "target": {}, "plot_die": False, "opportunity": None,
        "complication": None, "use_limit": None,
        "description": "Original summary.", "allow_multiple": False,
        "permissions": {},
    }


def adversary_payload(name="Rival"):
    return {
        "name": name, "role": "ordinary", "world": "roshar", "era": None,
        "level": 2,
        "defenses": {"physical": 13, "cognitive": 12, "spiritual": 11},
        "resources": {"health": {"current": 18, "max": 18}},
        "traits": ["human"], "equipment": [], "actions": [action()],
    }


def synthetic_catalog():
    build = CosmereBuild()
    package = next(row for row in build.profile.catalog()["packages"]
                   if row["id"] == "cosmere.stormlight.legacy")
    package_identity = {key: deepcopy(package[key])
                        for key in ("id", "version", "package_hash", "dependencies")}
    common = {
        "version": "1.0.0", "worlds": ["roshar"], "eras": [],
        "automation": "calculated", "audit_status": "provisional",
        "citations": [{"artifact_id": "stormlight.handbook",
                       "page_start": 10, "page_end": 10}],
        "package": package_identity,
    }
    table = {
        **common, "id": "test.table.weather", "kind": "table",
        "name": "Weather", "summary": "Weather table.",
        "spoiler_scope": "gm", "parameters": {"results": [
            {"id": "clear", "weight": 1, "value": {"text": "Clear"}}]},
    }
    item = {
        **common, "id": "test.item.visible", "kind": "item",
        "name": "Visible Item", "summary": "Player-visible item.",
        "spoiler_scope": "player", "parameters": {},
    }
    secret = {
        **common, "id": "test.item.secret", "kind": "item",
        "name": "Secret Item", "summary": "GM-only item.",
        "spoiler_scope": "gm", "parameters": {},
    }
    return {"rules_binding": to_dict(build.rules_binding),
            "packages": build.profile.catalog()["packages"],
            "catalogs": {"table": [table], "item": [item, secret]}}


class FakeProfile:
    def __init__(self, spoiler_scope="gm"):
        self.binding = CosmereBuild().rules_binding
        self.spoiler_scope = spoiler_scope

    def catalog(self):
        source = synthetic_catalog()
        if self.spoiler_scope != "gm":
            source["catalogs"]["table"] = []
            source["catalogs"]["item"] = [
                row for row in source["catalogs"]["item"]
                if row["spoiler_scope"] == "player"]
        return source

    def encounter_budget(self, participants, adversaries):
        return {"automation": "gm_adjudicated",
                "blocked_reasons": ["Use GM judgment."],
                "participant_count": len(participants),
                "adversary_count": len(adversaries)}


@pytest.fixture
def gm_api(tmp_path, monkeypatch):
    import app as host
    import systems.cosmere.rules.profiles as profiles

    saved_active = list(host.ACTIVE_ENCOUNTER)
    pc_dir = tmp_path / "pcs"
    pc_dir.mkdir()
    monkeypatch.setattr(host, "COSMERE_PC_DIR", str(pc_dir))
    monkeypatch.setattr(host, "COSMERE_ADVERSARIES_FILE", str(tmp_path / "adversaries.json"))
    monkeypatch.setattr(host, "_cosmere_table_state_path", lambda: str(tmp_path / "tables.json"),
                        raising=False)
    monkeypatch.setattr(host, "_cosmere_encounter_stage_path", lambda: str(tmp_path / "stage.json"),
                        raising=False)
    monkeypatch.setattr(host, "_cosmere_narrative_scene_path", lambda: str(tmp_path / "narrative.json"),
                        raising=False)
    monkeypatch.setattr(
        host, "_cosmere_narrative_transaction_path",
        lambda: str(tmp_path / "narrative_transaction.json"), raising=False)
    monkeypatch.setattr(host, "_cosmere_lifecycle_journal_path",
                        lambda campaign_id=None: str(tmp_path / "lifecycle.json"))
    monkeypatch.setattr(host, "_is_gm", lambda: True)
    monkeypatch.setattr(host, "_active_system", lambda: "cosmere")
    monkeypatch.setattr(host, "_load_campaign_config", lambda: {})
    monkeypatch.setattr(profiles, "resolve_profile",
                        lambda document=None, **options: FakeProfile(options.get("spoiler_scope", "player")))
    yield host, host.app.test_client(), tmp_path
    host.ACTIVE_ENCOUNTER[:] = saved_active


def test_table_roll_history_and_pins_persist_with_revision_and_replay(gm_api):
    host, client, tmp_path = gm_api
    rolled = client.post("/api/cosmere/tables/test.table.weather/roll", json={
        "seed": "session-1", "expected_revision": 0,
        "request_id": "request.table.roll"})
    assert rolled.status_code == 200, rolled.get_json()
    body = rolled.get_json()
    assert body["result"]["value"] == {"text": "Clear"}
    assert body["revision"] == 1 and body["replayed"] is False
    stored = json.loads((tmp_path / "tables.json").read_text(encoding="utf-8"))
    assert stored["revision"] == 1 and len(stored["history"]) == 1

    replay = client.post("/api/cosmere/tables/test.table.weather/roll", json={
        "seed": "session-1", "expected_revision": 0,
        "request_id": "request.table.roll"})
    assert replay.status_code == 200 and replay.get_json()["replayed"] is True
    roll_id = body["id"]
    pinned = client.post("/api/cosmere/tables/pins", json={
        "roll_id": roll_id, "expected_revision": 1,
        "request_id": "request.table.pin"})
    assert pinned.status_code == 200 and pinned.get_json()["revision"] == 2
    snapshot = client.get("/api/cosmere/tables/history").get_json()
    assert snapshot["history"][0]["id"] == roll_id
    assert snapshot["pins"][0]["roll_id"] == roll_id
    stale = client.post("/api/cosmere/tables/test.table.weather/roll", json={
        "seed": "session-2", "expected_revision": 1,
        "request_id": "request.table.stale"})
    assert stale.status_code == 409 and stale.get_json()["code"] == "stale_revision"

    generated = client.post("/api/cosmere/tables/test.table.weather/roll", json={
        "expected_revision": 2, "request_id": "request.table.generated"})
    assert generated.status_code == 200, generated.get_json()
    replayed = client.post("/api/cosmere/tables/test.table.weather/roll", json={
        "expected_revision": 2, "request_id": "request.table.generated"})
    assert replayed.status_code == 200, replayed.get_json()
    assert replayed.get_json()["replayed"] is True
    assert replayed.get_json()["seed"] == generated.get_json()["seed"]


def test_compendium_derives_audience_server_side_and_hidden_detail_is_404(gm_api, monkeypatch):
    host, client, _tmp_path = gm_api
    monkeypatch.setattr(host, "_is_gm", lambda: False)
    visible = client.get("/api/cosmere/compendium/search?q=item")
    assert visible.status_code == 200
    assert [row["id"] for row in visible.get_json()["items"]] == ["test.item.visible"]
    hidden = client.get("/api/cosmere/compendium/test.item.secret")
    assert hidden.status_code == 404
    # A client cannot promote itself with query parameters.
    assert client.get("/api/cosmere/compendium/test.item.secret?spoiler_scope=gm").status_code == 404
    monkeypatch.setattr(host, "_is_gm", lambda: True)
    assert client.get("/api/cosmere/compendium/test.item.secret").status_code == 200


def test_party_projection_and_existing_export_delegate_to_transfer(gm_api, monkeypatch):
    host, client, _tmp_path = gm_api
    pid = uuid.uuid4().hex
    host._save_cosmere_pc({"id": pid, "system": "cosmere", "name": "Hero",
                           "build": base_build(), "runtime_state": {"health": 5}})
    party = client.get("/api/cosmere/party/capabilities")
    assert party.status_code == 200
    member = party.get_json()["members"][0]
    assert member["id"] == pid and member["resources"]["health"]["current"] == 5
    assert "build" not in member and "notes" not in member

    import systems.cosmere.transfer as transfer
    calls = []
    real = transfer.export_character
    monkeypatch.setattr(transfer, "export_character",
                        lambda document, **options: (calls.append(document["id"]),
                                                     real(document, **options))[1])
    exported = client.get(f"/api/cosmere/pc/{pid}/export")
    assert exported.status_code == 200 and calls == [pid]


def test_encounter_preview_and_stage_resolve_server_records_then_write_before_broadcast(gm_api, monkeypatch):
    host, client, tmp_path = gm_api
    pid = uuid.uuid4().hex
    host._save_cosmere_pc({"id": pid, "system": "cosmere", "name": "Hero",
                           "build": base_build(), "runtime_state": {"health": 5}})
    state, created = create_adversary(
        {}, adversary_payload(), binding=to_dict(CosmereBuild().rules_binding),
        expected_store_revision=0, request_id="request.setup.adversary",
        id_factory=lambda: "ab" * 16)
    (tmp_path / "adversaries.json").write_text(
        json.dumps(state), encoding="utf-8")
    aid = created["adversary"]["id"]

    preview = client.post("/api/cosmere/encounters/preview", json={
        "participant_ids": [pid], "adversary_ids": [aid],
        "participants": [{"id": "forged", "ac": 99}]})
    assert preview.status_code == 200, preview.get_json()
    data = preview.get_json()
    assert data["participants"][0]["id"] == pid
    assert data["adversaries"][0]["id"] == aid
    assert data["budget"]["automation"] == "gm_adjudicated"
    assert "xp" not in json.dumps(data).lower()

    events = []
    real_write = host._atomic_write_json
    monkeypatch.setattr(host, "_atomic_write_json", lambda path, value, **options: (
        real_write(path, value, **options), events.append("write"))[0])
    monkeypatch.setattr(host, "sse_broadcast",
                        lambda *args, **kwargs: events.append("broadcast"))
    staged = client.post("/api/cosmere/encounters/stage", json={
        "participant_ids": [pid], "adversary_ids": [aid],
        "expected_revision": 0, "request_id": "request.stage.encounter"})
    assert staged.status_code == 200, staged.get_json()
    assert events == ["write", "broadcast"]
    assert (tmp_path / "stage.json").is_file()
    assert client.get("/api/cosmere/encounters/stage").get_json()["stage"]["id"].startswith("stage.")


def test_encounter_legacy_fallback_cannot_be_relabelled_into_an_ineligible_binding(
        gm_api, monkeypatch):
    host, client, _tmp_path = gm_api

    class ScadrialProfile:
        binding = SimpleNamespace(default_world="scadrial", allowed_eras=("era-1",))

        def catalog(self):
            return {"packages": [{"id": "cosmere.core", "version": "1.0.0",
                                   "package_hash": "a" * 64,
                                   "dependencies": [], "worlds": ["cosmere"],
                                   "eras": []}], "catalogs": {}}

        def encounter_budget(self, participants, adversaries):
            return {"automation": "gm_adjudicated"}

    legacy = SimpleNamespace(
        name="Wrong World", is_pc=False, role="ordinary",
        defenses={"phy": 10, "cog": 10, "spi": 10},
        current_hp=10, health_max=10, current_focus=0, focus_max=0,
        current_investiture=0, investiture_max=0,
        actions=[], strikes=[], traits=[])
    monkeypatch.setattr(host, "_cosmere_service_profile",
                        lambda **_options: ScadrialProfile())
    monkeypatch.setattr(host, "_load_campaign_config",
                        lambda: {"cosmere_era": "era-1"})
    monkeypatch.setattr(host, "_cosmere_combatant", lambda _actor_id: legacy)

    response = client.post("/api/cosmere/encounters/preview", json={
        "participant_ids": [], "adversary_ids": ["legacy.wrong-world"],
        "era": "era-1"})
    assert response.status_code == 422
    assert "not found" in response.get_json()["error"].lower()


def test_rich_adversary_api_crud_legacy_quick_create_and_reference_protection(
        gm_api, monkeypatch):
    host, client, tmp_path = gm_api
    created = client.post("/api/cosmere/adversaries/custom", json={
        **adversary_payload(), "expected_store_revision": 0,
        "request_id": "request.api.adversary.create"})
    assert created.status_code == 200, created.get_json()
    adversary = created.get_json()["adversary"]
    assert adversary["actions"][0]["activation"]["cost"]["value"] == 1
    listed = client.get("/api/cosmere/adversaries/custom").get_json()
    assert listed["adversaries"][0]["id"] == adversary["id"]

    edited = client.put(f"/api/cosmere/adversaries/custom/{adversary['id']}", json={
        **adversary_payload("Veteran"), "expected_revision": 0,
        "request_id": "request.api.adversary.edit"})
    assert edited.status_code == 200 and edited.get_json()["adversary"]["revision"] == 1
    stale = client.put(f"/api/cosmere/adversaries/custom/{adversary['id']}", json={
        **adversary_payload("Stale"), "expected_revision": 0,
        "request_id": "request.api.adversary.stale"})
    assert stale.status_code == 409

    # A staged reference blocks deletion.
    (tmp_path / "stage.json").write_text(json.dumps({
        "schema_version": 1, "revision": 1, "receipts": [],
        "stage": {"adversaries": [{"id": adversary["id"]}]}}), encoding="utf-8")
    blocked = client.delete(f"/api/cosmere/adversaries/custom/{adversary['id']}", json={
        "expected_revision": 1, "request_id": "request.api.adversary.delete"})
    assert blocked.status_code == 409 and blocked.get_json()["code"] == "adversary_referenced"

    # Live and persisted tactical references are protected independently of
    # the neutral staged-encounter record.
    (tmp_path / "stage.json").write_text(json.dumps({
        "schema_version": 1, "revision": 2, "receipts": [], "stage": None}),
        encoding="utf-8")
    saved_active = list(host.ACTIVE_ENCOUNTER)
    host.ACTIVE_ENCOUNTER[:] = [SimpleNamespace(restore_id=adversary["id"])]
    try:
        blocked_live = client.delete(
            f"/api/cosmere/adversaries/custom/{adversary['id']}", json={
                "expected_revision": 1,
                "request_id": "request.api.adversary.delete.live"})
        assert blocked_live.status_code == 409
    finally:
        host.ACTIVE_ENCOUNTER[:] = saved_active

    encounter_dir = tmp_path / "encounters"
    encounter_dir.mkdir()
    (encounter_dir / "_autosave.json").write_text(json.dumps({
        "combatants": [{"system": "cosmere", "cosmere_id": adversary["id"]}]}),
        encoding="utf-8")
    monkeypatch.setattr(host, "ENCOUNTER_DIR", str(encounter_dir))
    blocked_saved = client.delete(
        f"/api/cosmere/adversaries/custom/{adversary['id']}", json={
            "expected_revision": 1,
            "request_id": "request.api.adversary.delete.saved"})
    assert blocked_saved.status_code == 409

    # Existing quick-create remains reusable by the tracker and writes the new envelope.
    quick = client.post("/api/cosmere/add_custom_adversary", json={
        "name": "Quick Rival", "phy": 12, "cog": 11, "spi": 10,
        "health": 15, "atk_name": "Strike", "atk_mod": 5,
        "atk_dmg": "1d6"})
    assert quick.status_code == 200, quick.get_json()
    assert host._cosmere_doc_by_id(quick.get_json()["id"])["name"] == "Quick Rival"
    assert isinstance(json.loads((tmp_path / "adversaries.json").read_text(encoding="utf-8")), dict)


def test_custom_adversary_delete_fails_closed_for_corrupt_persisted_encounter(
        gm_api, monkeypatch):
    host, client, tmp_path = gm_api
    created = client.post("/api/cosmere/adversaries/custom", json={
        **adversary_payload(), "expected_store_revision": 0,
        "request_id": "request.api.adversary.create.corrupt-reference"})
    assert created.status_code == 200, created.get_json()
    adversary_id = created.get_json()["adversary"]["id"]

    encounter_dir = tmp_path / "encounters"
    encounter_dir.mkdir()
    (encounter_dir / "corrupt.json").write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(host, "ENCOUNTER_DIR", str(encounter_dir))

    rejected = client.delete(
        f"/api/cosmere/adversaries/custom/{adversary_id}", json={
            "expected_revision": 0,
            "request_id": "request.api.adversary.delete.corrupt-reference"})

    assert rejected.status_code == 400
    assert [row["id"] for row in client.get(
        "/api/cosmere/adversaries/custom").get_json()["adversaries"]] == [
            adversary_id]


@pytest.mark.parametrize("scene_type", ["conversation", "endeavor"])
def test_narrative_scene_api_persists_revisions_and_close_expires_scene_effects(gm_api, scene_type):
    host, client, tmp_path = gm_api
    pid = uuid.uuid4().hex
    host._save_cosmere_pc({
        "id": pid, "system": "cosmere", "name": "Hero", "build": base_build(),
        "runtime_state": {"health": 5, "effects": [{
            "id": "effect.scene.api", "source_id": "source.scene.api",
            "target_id": pid, "parameters": {}, "selected_attribute": None,
            "stacking_identity": "effect.scene.api.stack",
            "duration_unit": "scene", "remaining_duration": 1,
            "trigger": "scene_end", "refresh_policy": "independent",
            "expiry_state": {}, "expired": False}]}})
    unrelated_id = uuid.uuid4().hex
    host._save_cosmere_pc({
        "id": unrelated_id, "system": "cosmere", "name": "Other Hero",
        "build": base_build("Other Hero"),
        "runtime_state": {"health": 5, "scene": {
            "id": "scene.unrelated", "uses": {"recover": 1}}, "effects": [{
                "id": "effect.scene.unrelated",
                "source_id": "source.scene.unrelated",
                "target_id": unrelated_id, "parameters": {},
                "selected_attribute": None,
                "stacking_identity": "effect.scene.unrelated.stack",
                "duration_unit": "scene", "remaining_duration": 1,
                "trigger": "scene_end", "refresh_policy": "independent",
                "expiry_state": {}, "expired": False}]}})
    track = "disposition" if scene_type == "conversation" else "progress"
    payload = {"name": "Scene", "world": "roshar", "era": None,
               "participants": [{"actor_id": pid, "role": "lead"}],
               "tracks": {track: {"value": 0, "target": 3}},
               "expected_store_revision": 0,
               "request_id": f"request.api.{scene_type}.create"}
    created = client.post(f"/api/cosmere/narrative-scenes/{scene_type}", json=payload)
    assert created.status_code == 200, created.get_json()
    scene = created.get_json()["scene"]
    assigned_scope = host._load_cosmere_pc(pid)["runtime_state"]["scene"]
    assert assigned_scope["id"] == scene["id"]
    assert assigned_scope["type"] == scene_type
    payload["tracks"][track]["value"] = 1
    payload["expected_revision"] = 0
    payload["request_id"] = f"request.api.{scene_type}.update"
    updated = client.put(f"/api/cosmere/narrative-scenes/{scene['id']}", json=payload)
    assert updated.status_code == 200 and updated.get_json()["scene"]["revision"] == 1
    closed = client.post(f"/api/cosmere/narrative-scenes/{scene['id']}/close", json={
        "expected_revision": 1, "request_id": f"request.api.{scene_type}.close"})
    assert closed.status_code == 200, closed.get_json()
    assert closed.get_json()["lifecycle"]["trigger"] == "scene_end"
    participant = host._load_cosmere_pc(pid)
    participant_revision = participant["revision"]
    assert participant["runtime_state"]["effects"] == []
    assert participant["runtime_state"]["scene"]["id"] is None
    assert "type" not in participant["runtime_state"]["scene"]
    unrelated = host._load_cosmere_pc(unrelated_id)
    assert [row["id"] for row in unrelated["runtime_state"]["effects"]] == [
        "effect.scene.unrelated"]
    assert unrelated["runtime_state"]["scene"] == {
        "id": "scene.unrelated", "uses": {"recover": 1}}
    stored = json.loads((tmp_path / "narrative.json").read_text(encoding="utf-8"))
    assert stored["scenes"][0]["status"] == "closed"
    assert not (tmp_path / "lifecycle.json").exists()

    replayed = client.post(f"/api/cosmere/narrative-scenes/{scene['id']}/close", json={
        "expected_revision": 1,
        "request_id": f"request.api.{scene_type}.close"})
    assert replayed.status_code == 200 and replayed.get_json()["replayed"] is True
    assert host._load_cosmere_pc(pid)["revision"] == participant_revision
    assert not (tmp_path / "lifecycle.json").exists()


def test_narrative_participants_must_match_specific_world_and_era_scope(
        gm_api, monkeypatch):
    host, _client, _tmp_path = gm_api
    roshar_id = uuid.uuid4().hex
    era1_id = uuid.uuid4().hex
    documents = {
        roshar_id: {
            "id": roshar_id, "origin_world": "roshar", "era": None,
            "build": {"origin_world": "roshar", "era": None},
            "runtime_state": {},
        },
        era1_id: {
            "id": era1_id, "origin_world": "scadrial", "era": "era1",
            "build": {"origin_world": "scadrial", "era": "era1"},
            "runtime_state": {},
        },
    }
    monkeypatch.setattr(host, "_load_cosmere_pc", documents.get)

    with pytest.raises(host._CosmereRulesValidationError, match="world"):
        host._cosmere_narrative_scope_documents({
            "id": "scene.scope.roshar", "world": "roshar", "era": None,
            "participants": [{"actor_id": era1_id}],
        })
    with pytest.raises(host._CosmereRulesValidationError, match="era"):
        host._cosmere_narrative_scope_documents({
            "id": "scene.scope.era2", "world": "scadrial", "era": "era2",
            "participants": [{"actor_id": era1_id}],
        })

    cross_world = host._cosmere_narrative_scope_documents({
        "id": "scene.scope.cosmere", "world": "cosmere", "era": None,
        "participants": [
            {"actor_id": roshar_id}, {"actor_id": era1_id},
        ],
    })
    assert [row["id"] for row in cross_world] == [roshar_id, era1_id]


def test_narrative_and_central_lifecycle_journals_are_mutually_exclusive(
        gm_api, monkeypatch):
    host, client, tmp_path = gm_api
    pid = uuid.uuid4().hex
    host._save_cosmere_pc({
        "id": pid, "system": "cosmere", "name": "Hero", "build": base_build(),
        "runtime_state": {"health": 5},
    })
    original_payload = {
        "name": "Existing Scene", "world": "roshar", "era": None,
        "participants": [{"actor_id": pid, "role": "lead"}],
        "tracks": {"disposition": {"value": 0, "target": 3}},
        "expected_store_revision": 0,
        "request_id": "request.journal.existing.create",
    }
    existing = client.post(
        "/api/cosmere/narrative-scenes/conversation", json=original_payload)
    assert existing.status_code == 200, existing.get_json()
    scene_id = existing.get_json()["scene"]["id"]
    store_before = (tmp_path / "narrative.json").read_bytes()

    central = host._begin_cosmere_lifecycle_transition(
        "encounter_clear", triggers=("scene_end", "encounter_end"),
        source_scope_id="active-encounter", target_scope_id=None,
        campaign_id=host._loaded_campaign_id())
    blocked_create = client.post(
        "/api/cosmere/narrative-scenes/conversation", json={
            **original_payload, "name": "Blocked Create",
            "participants": [], "expected_store_revision": 1,
            "request_id": "request.journal.blocked.create",
        })
    blocked_update = client.put(
        f"/api/cosmere/narrative-scenes/{scene_id}", json={
            **original_payload, "name": "Blocked Update",
            "expected_revision": 0,
            "request_id": "request.journal.blocked.update",
        })
    blocked_close = client.post(
        f"/api/cosmere/narrative-scenes/{scene_id}/close", json={
            "expected_revision": 0,
            "request_id": "request.journal.blocked.close",
        })
    for response in (blocked_create, blocked_update, blocked_close):
        assert response.status_code == 409, response.get_json()
        assert response.get_json()["code"] == "lifecycle_transition_pending"
    assert (tmp_path / "narrative.json").read_bytes() == store_before
    assert not (tmp_path / "narrative_transaction.json").exists()

    host._complete_cosmere_lifecycle_transition(
        central["boundary_id"], campaign_id=host._loaded_campaign_id())
    pending_scene = {
        "id": "scene.pending.journal", "type": "conversation",
    }
    host._begin_cosmere_narrative_transaction(
        kind="create", payload={"request_id": "request.journal.pending"},
        previous_scene={}, target_scene=pending_scene, target_state={},
        result={"scene": pending_scene})
    monkeypatch.setattr(host._scenes, "table_scene_id", lambda _cid: "scene.old")
    monkeypatch.setattr(
        host._scenes, "load_scene",
        lambda _cid, requested: {"id": requested, "name": "Tactical"})
    monkeypatch.setattr(
        host._scenes, "set_table_scene",
        lambda *_args: pytest.fail("pending narrative journal must block activation"))

    blocked_activate = client.post("/api/scenes/scene.next/activate")
    blocked_clear = client.post("/api/clear_encounter")
    for response in (blocked_activate, blocked_clear):
        assert response.status_code == 409, response.get_json()
        assert response.get_json()["code"] == "narrative_transaction_pending"
    assert (tmp_path / "narrative_transaction.json").exists()
    assert not (tmp_path / "lifecycle.json").exists()


def test_narrative_contribution_advances_only_its_actor_with_cas_and_replay(gm_api):
    host, client, _tmp_path = gm_api
    pid = uuid.uuid4().hex
    other_id = uuid.uuid4().hex

    def participant(actor_id, name):
        return {
            "id": actor_id, "system": "cosmere", "name": name,
            "build": base_build(name),
            "runtime_state": {"health": 5, "effects": [{
                "id": f"effect.contribution.actor-{actor_id}",
                "source_id": "source.contribution.api",
                "target_id": actor_id, "parameters": {},
                "selected_attribute": None,
                "stacking_identity": f"effect.contribution.actor-{actor_id}.stack",
                "duration_unit": "contribution", "remaining_duration": 2,
                "trigger": "contribution_end", "refresh_policy": "independent",
                "expiry_state": {"actor_id": actor_id}, "expired": False,
            }]},
        }

    host._save_cosmere_pc(participant(pid, "Lead"))
    host._save_cosmere_pc(participant(other_id, "Support"))
    created = client.post("/api/cosmere/narrative-scenes/conversation", json={
        "name": "Parley", "world": "roshar", "era": None,
        "participants": [
            {"actor_id": pid, "role": "lead"},
            {"actor_id": other_id, "role": "support"},
        ],
        "tracks": {"disposition": {"value": 0, "target": 3}},
        "expected_store_revision": 0,
        "request_id": "request.contribution.scene.create",
    })
    assert created.status_code == 200, created.get_json()
    scene_id = created.get_json()["scene"]["id"]
    revision = host._load_cosmere_pc(pid)["revision"]
    other_revision = host._load_cosmere_pc(other_id)["revision"]
    payload = {
        "expected_revision": revision,
        "request_id": "request.contribution.lead.one",
    }

    advanced = client.post(
        f"/api/cosmere/narrative-scenes/{scene_id}/participants/{pid}/contribution",
        json=payload)
    replayed = client.post(
        f"/api/cosmere/narrative-scenes/{scene_id}/participants/{pid}/contribution",
        json=payload)

    assert advanced.status_code == 200, advanced.get_json()
    assert advanced.get_json()["revision"] == revision + 1
    assert advanced.get_json()["expired"] == []
    assert replayed.status_code == 200 and replayed.get_json()["replayed"] is True
    assert host._load_cosmere_pc(pid)["runtime_state"]["effects"][0][
        "remaining_duration"] == 1
    assert host._load_cosmere_pc(other_id)["revision"] == other_revision
    assert host._load_cosmere_pc(other_id)["runtime_state"]["effects"][0][
        "remaining_duration"] == 2

    stale = client.post(
        f"/api/cosmere/narrative-scenes/{scene_id}/participants/{pid}/contribution",
        json={"expected_revision": revision,
              "request_id": "request.contribution.lead.stale"})
    assert stale.status_code == 409
    assert stale.get_json()["code"] == "stale_revision"


def test_narrative_contribution_resumes_pending_live_coordination_without_reapply(
        gm_api, monkeypatch):
    host, client, _tmp_path = gm_api
    pid = uuid.uuid4().hex
    host._save_cosmere_pc({
        "id": pid, "system": "cosmere", "name": "Lead",
        "build": base_build("Lead"),
        "runtime_state": {"health": 5, "effects": [{
            "id": "effect.contribution.resume",
            "source_id": "source.contribution.resume",
            "target_id": pid, "parameters": {}, "selected_attribute": None,
            "stacking_identity": "effect.contribution.resume.stack",
            "duration_unit": "contribution", "remaining_duration": 2,
            "trigger": "contribution_end", "refresh_policy": "independent",
            "expiry_state": {"actor_id": pid}, "expired": False,
        }]},
    })
    created = client.post("/api/cosmere/narrative-scenes/conversation", json={
        "name": "Parley", "world": "roshar", "era": None,
        "participants": [{"actor_id": pid, "role": "lead"}],
        "tracks": {"disposition": {"value": 0, "target": 3}},
        "expected_store_revision": 0,
        "request_id": "request.contribution.resume.scene",
    })
    assert created.status_code == 200, created.get_json()
    scene_id = created.get_json()["scene"]["id"]
    revision = host._load_cosmere_pc(pid)["revision"]
    persistence = iter([False, True])
    broadcasts = []
    monkeypatch.setattr(host, "_sync_cosmere_combatant_state", lambda *_a, **_k: True)
    monkeypatch.setattr(
        host, "_do_persist_encounter_state_unlocked", lambda: next(persistence))
    monkeypatch.setattr(
        host, "_do_broadcast_encounter_state",
        lambda: broadcasts.append("encounter"))
    monkeypatch.setattr(
        host, "_cosmere_advancement_broadcast",
        lambda _document: broadcasts.append("player"))
    payload = {
        "expected_revision": revision,
        "request_id": "request.contribution.resume.once",
    }

    failed = client.post(
        f"/api/cosmere/narrative-scenes/{scene_id}/participants/{pid}/contribution",
        json=payload)

    assert failed.status_code == 503
    after_failure = host._load_cosmere_pc(pid)
    assert after_failure["revision"] == revision + 1
    assert after_failure["runtime_state"]["effects"][0]["remaining_duration"] == 1
    receipt = after_failure["runtime_state"]["lifecycle_receipts"][-1]
    assert receipt["coordination_status"] == "pending"
    assert broadcasts == []

    resumed = client.post(
        f"/api/cosmere/narrative-scenes/{scene_id}/participants/{pid}/contribution",
        json=payload)

    assert resumed.status_code == 200, resumed.get_json()
    assert resumed.get_json()["replayed"] is True
    after_resume = host._load_cosmere_pc(pid)
    assert after_resume["revision"] == revision + 1
    assert after_resume["runtime_state"]["effects"][0]["remaining_duration"] == 1
    assert after_resume["runtime_state"]["lifecycle_receipts"][-1][
        "coordination_status"] == "complete"
    assert broadcasts == ["encounter", "player"]


def test_narrative_create_replay_after_close_does_not_restore_closed_scope(gm_api):
    host, client, _tmp_path = gm_api
    pid = uuid.uuid4().hex
    host._save_cosmere_pc({
        "id": pid, "system": "cosmere", "name": "Hero", "build": base_build(),
        "runtime_state": {"health": 5}})
    payload = {
        "name": "Parley", "world": "roshar", "era": None,
        "participants": [{"actor_id": pid, "role": "lead"}],
        "tracks": {"disposition": {"value": 0, "target": 3}},
        "expected_store_revision": 0,
        "request_id": "request.api.scene.create.replay-after-close",
    }
    created = client.post("/api/cosmere/narrative-scenes/conversation", json=payload)
    assert created.status_code == 200, created.get_json()
    scene_id = created.get_json()["scene"]["id"]
    closed = client.post(f"/api/cosmere/narrative-scenes/{scene_id}/close", json={
        "expected_revision": 0,
        "request_id": "request.api.scene.close.before-create-replay"})
    assert closed.status_code == 200, closed.get_json()
    after_close = host._load_cosmere_pc(pid)
    assert after_close["runtime_state"]["scene"]["id"] is None

    replayed = client.post("/api/cosmere/narrative-scenes/conversation", json=payload)

    assert replayed.status_code == 200 and replayed.get_json()["replayed"] is True
    after_replay = host._load_cosmere_pc(pid)
    assert after_replay["revision"] == after_close["revision"]
    assert after_replay["runtime_state"] == after_close["runtime_state"]


def test_narrative_create_conflict_does_not_persist_rejected_scene(gm_api):
    host, client, tmp_path = gm_api
    pid = uuid.uuid4().hex
    host._save_cosmere_pc({
        "id": pid, "system": "cosmere", "name": "Hero", "build": base_build(),
        "runtime_state": {"health": 5}})
    first_payload = {
        "name": "First Scene", "world": "roshar", "era": None,
        "participants": [{"actor_id": pid, "role": "lead"}],
        "tracks": {"disposition": {"value": 0, "target": 3}},
        "expected_store_revision": 0,
        "request_id": "request.api.scene.create.first",
    }
    first = client.post(
        "/api/cosmere/narrative-scenes/conversation", json=first_payload)
    assert first.status_code == 200, first.get_json()
    stored_before = json.loads(
        (tmp_path / "narrative.json").read_text(encoding="utf-8"))
    participant_before = host._load_cosmere_pc(pid)

    rejected = client.post("/api/cosmere/narrative-scenes/conversation", json={
        **first_payload, "name": "Rejected Scene", "expected_store_revision": 1,
        "request_id": "request.api.scene.create.rejected"})

    assert rejected.status_code == 400
    assert json.loads((tmp_path / "narrative.json").read_text(
        encoding="utf-8")) == stored_before
    assert host._load_cosmere_pc(pid) == participant_before


def test_narrative_create_rolls_back_scope_when_scene_persistence_fails(
        gm_api, monkeypatch):
    host, client, tmp_path = gm_api
    pid = uuid.uuid4().hex
    host._save_cosmere_pc({
        "id": pid, "system": "cosmere", "name": "Hero", "build": base_build(),
        "runtime_state": {"health": 5}})
    participant_before = host._load_cosmere_pc(pid)
    actor = host._cosmere_combatant(pid)
    actor_before = deepcopy(actor.__dict__)
    saved_active = list(host.ACTIVE_ENCOUNTER)
    host.ACTIVE_ENCOUNTER[:] = [actor]
    payload = {
        "name": "Recoverable Scene", "world": "roshar", "era": None,
        "participants": [{"actor_id": pid, "role": "lead"}],
        "tracks": {"disposition": {"value": 0, "target": 3}},
        "expected_store_revision": 0,
        "request_id": "request.api.scene.create.persistence-failure",
    }
    narrative_path = str(tmp_path / "narrative.json")
    real_write = host._atomic_write_json
    failures = {"remaining": 1}

    def fail_first_scene_write(path, value, **options):
        if str(path) == narrative_path and failures["remaining"]:
            failures["remaining"] -= 1
            real_write(path, value, **options)
            raise OSError("injected narrative persistence failure")
        return real_write(path, value, **options)

    monkeypatch.setattr(host, "_atomic_write_json", fail_first_scene_write)

    failed = client.post(
        "/api/cosmere/narrative-scenes/conversation", json=payload)

    assert failed.status_code == 500
    assert not (tmp_path / "narrative.json").exists()
    assert host._load_cosmere_pc(pid) == participant_before
    assert actor.__dict__ == actor_before

    replayed = client.post(
        "/api/cosmere/narrative-scenes/conversation", json=payload)
    assert replayed.status_code == 200, replayed.get_json()
    scene_id = replayed.get_json()["scene"]["id"]
    assert host._load_cosmere_pc(pid)["runtime_state"]["scene"]["id"] == scene_id
    host.ACTIVE_ENCOUNTER[:] = saved_active


def test_narrative_create_resumes_durable_transaction_after_interruption(
        gm_api, monkeypatch):
    host, client, tmp_path = gm_api
    pid = uuid.uuid4().hex
    host._save_cosmere_pc({
        "id": pid, "system": "cosmere", "name": "Hero", "build": base_build(),
        "runtime_state": {"health": 5}})
    payload = {
        "name": "Restartable Scene", "world": "roshar", "era": None,
        "participants": [{"actor_id": pid, "role": "lead"}],
        "tracks": {"disposition": {"value": 0, "target": 3}},
        "expected_store_revision": 0,
        "request_id": "request.api.scene.create.interrupted",
    }
    narrative_path = str(tmp_path / "narrative.json")
    real_write = host._atomic_write_json
    interruptions = {"remaining": 1}

    def interrupt_first_scene_write(path, value, **options):
        if str(path) == narrative_path and interruptions["remaining"]:
            interruptions["remaining"] -= 1
            raise SystemExit("injected process interruption")
        return real_write(path, value, **options)

    monkeypatch.setattr(host, "_atomic_write_json", interrupt_first_scene_write)

    with pytest.raises(SystemExit, match="process interruption"):
        client.post("/api/cosmere/narrative-scenes/conversation", json=payload)

    transaction_path = tmp_path / "narrative_transaction.json"
    assert transaction_path.is_file()
    interrupted = host._load_cosmere_pc(pid)
    scene_id = interrupted["runtime_state"]["scene"]["id"]
    interrupted_revision = interrupted["revision"]
    assert not (tmp_path / "narrative.json").exists()

    resumed = client.post(
        "/api/cosmere/narrative-scenes/conversation", json=payload)
    assert resumed.status_code == 200, resumed.get_json()
    assert resumed.get_json()["replayed"] is True
    assert resumed.get_json()["scene"]["id"] == scene_id
    assert host._load_cosmere_pc(pid)["revision"] == interrupted_revision
    assert not transaction_path.exists()


def test_narrative_update_reconciles_removed_and_added_participant_scopes(gm_api):
    host, client, _tmp_path = gm_api
    removed_id = uuid.uuid4().hex
    added_id = uuid.uuid4().hex
    host._save_cosmere_pc({
        "id": removed_id, "system": "cosmere", "name": "Departing Hero",
        "build": base_build("Departing Hero"),
        "runtime_state": {"health": 5, "effects": [{
            "id": "effect.scene.membership", "source_id": "source.scene.membership",
            "target_id": removed_id, "parameters": {}, "selected_attribute": None,
            "stacking_identity": "effect.scene.membership.stack",
            "duration_unit": "scene", "remaining_duration": 1,
            "trigger": "scene_end", "refresh_policy": "independent",
            "expiry_state": {}, "expired": False}]}})
    host._save_cosmere_pc({
        "id": added_id, "system": "cosmere", "name": "Joining Hero",
        "build": base_build("Joining Hero"), "runtime_state": {"health": 5}})
    create_payload = {
        "name": "Changing Cast", "world": "roshar", "era": None,
        "participants": [{"actor_id": removed_id, "role": "lead"}],
        "tracks": {"disposition": {"value": 0, "target": 3}},
        "expected_store_revision": 0,
        "request_id": "request.api.scene.membership.create",
    }
    created = client.post(
        "/api/cosmere/narrative-scenes/conversation", json=create_payload)
    assert created.status_code == 200, created.get_json()
    scene_id = created.get_json()["scene"]["id"]

    updated = client.put(f"/api/cosmere/narrative-scenes/{scene_id}", json={
        **create_payload,
        "participants": [{"actor_id": added_id, "role": "support"}],
        "expected_revision": 0,
        "request_id": "request.api.scene.membership.update",
    })

    assert updated.status_code == 200, updated.get_json()
    removed = host._load_cosmere_pc(removed_id)
    assert removed["runtime_state"]["scene"]["id"] is None
    assert removed["runtime_state"]["effects"] == []
    assert host._load_cosmere_pc(added_id)["runtime_state"]["scene"]["id"] == scene_id
    removed_after_update = deepcopy(removed)

    closed = client.post(f"/api/cosmere/narrative-scenes/{scene_id}/close", json={
        "expected_revision": 1,
        "request_id": "request.api.scene.membership.close"})
    assert closed.status_code == 200, closed.get_json()
    assert host._load_cosmere_pc(removed_id) == removed_after_update
    assert host._load_cosmere_pc(added_id)["runtime_state"]["scene"]["id"] is None


def test_narrative_update_rolls_back_scope_when_scene_persistence_fails(
        gm_api, monkeypatch):
    host, client, tmp_path = gm_api
    removed_id = uuid.uuid4().hex
    added_id = uuid.uuid4().hex
    for pid, name in ((removed_id, "Departing Hero"),
                      (added_id, "Joining Hero")):
        host._save_cosmere_pc({
            "id": pid, "system": "cosmere", "name": name,
            "build": base_build(name), "runtime_state": {"health": 5}})
    created = client.post("/api/cosmere/narrative-scenes/conversation", json={
        "name": "Changing Cast", "world": "roshar", "era": None,
        "participants": [{"actor_id": removed_id, "role": "lead"}],
        "tracks": {"disposition": {"value": 0, "target": 3}},
        "expected_store_revision": 0,
        "request_id": "request.api.scene.update-failure.create",
    })
    assert created.status_code == 200, created.get_json()
    scene_id = created.get_json()["scene"]["id"]
    store_before = json.loads(
        (tmp_path / "narrative.json").read_text(encoding="utf-8"))
    removed_before = host._load_cosmere_pc(removed_id)
    added_before = host._load_cosmere_pc(added_id)
    removed_actor = host._cosmere_combatant(removed_id)
    added_actor = host._cosmere_combatant(added_id)
    live_before = [deepcopy(removed_actor.__dict__), deepcopy(added_actor.__dict__)]
    saved_active = list(host.ACTIVE_ENCOUNTER)
    host.ACTIVE_ENCOUNTER[:] = [removed_actor, added_actor]
    update_payload = {
        "name": "Changing Cast", "world": "roshar", "era": None,
        "participants": [{"actor_id": added_id, "role": "support"}],
        "tracks": {"disposition": {"value": 1, "target": 3}},
        "expected_revision": 0,
        "request_id": "request.api.scene.update.persistence-failure",
    }
    narrative_path = str(tmp_path / "narrative.json")
    real_write = host._atomic_write_json
    failures = {"remaining": 1}

    def fail_first_scene_write(path, value, **options):
        if str(path) == narrative_path and failures["remaining"]:
            failures["remaining"] -= 1
            raise OSError("injected narrative persistence failure")
        return real_write(path, value, **options)

    monkeypatch.setattr(host, "_atomic_write_json", fail_first_scene_write)

    failed = client.put(
        f"/api/cosmere/narrative-scenes/{scene_id}", json=update_payload)

    assert failed.status_code == 500
    assert json.loads((tmp_path / "narrative.json").read_text(
        encoding="utf-8")) == store_before
    assert host._load_cosmere_pc(removed_id) == removed_before
    assert host._load_cosmere_pc(added_id) == added_before
    assert [removed_actor.__dict__, added_actor.__dict__] == live_before

    replayed = client.put(
        f"/api/cosmere/narrative-scenes/{scene_id}", json=update_payload)
    assert replayed.status_code == 200, replayed.get_json()
    assert host._load_cosmere_pc(removed_id)["runtime_state"]["scene"]["id"] is None
    assert host._load_cosmere_pc(added_id)["runtime_state"]["scene"]["id"] == scene_id
    host.ACTIVE_ENCOUNTER[:] = saved_active


def test_narrative_update_resumes_durable_transaction_after_interruption(
        gm_api, monkeypatch):
    host, client, tmp_path = gm_api
    removed_id = uuid.uuid4().hex
    added_id = uuid.uuid4().hex
    for pid, name in ((removed_id, "Departing Hero"),
                      (added_id, "Joining Hero")):
        host._save_cosmere_pc({
            "id": pid, "system": "cosmere", "name": name,
            "build": base_build(name), "runtime_state": {"health": 5}})
    created = client.post("/api/cosmere/narrative-scenes/conversation", json={
        "name": "Changing Cast", "world": "roshar", "era": None,
        "participants": [{"actor_id": removed_id, "role": "lead"}],
        "tracks": {"disposition": {"value": 0, "target": 3}},
        "expected_store_revision": 0,
        "request_id": "request.api.scene.interrupted-update.create",
    })
    assert created.status_code == 200, created.get_json()
    scene_id = created.get_json()["scene"]["id"]
    update_payload = {
        "name": "Changing Cast", "world": "roshar", "era": None,
        "participants": [{"actor_id": added_id, "role": "support"}],
        "tracks": {"disposition": {"value": 1, "target": 3}},
        "expected_revision": 0,
        "request_id": "request.api.scene.update.interrupted",
    }
    narrative_path = str(tmp_path / "narrative.json")
    real_write = host._atomic_write_json
    interruptions = {"remaining": 1}

    def interrupt_first_scene_write(path, value, **options):
        if str(path) == narrative_path and interruptions["remaining"]:
            interruptions["remaining"] -= 1
            raise SystemExit("injected process interruption")
        return real_write(path, value, **options)

    monkeypatch.setattr(host, "_atomic_write_json", interrupt_first_scene_write)

    with pytest.raises(SystemExit, match="process interruption"):
        client.put(f"/api/cosmere/narrative-scenes/{scene_id}", json=update_payload)

    transaction_path = tmp_path / "narrative_transaction.json"
    assert transaction_path.is_file()
    removed_revision = host._load_cosmere_pc(removed_id)["revision"]
    added_revision = host._load_cosmere_pc(added_id)["revision"]
    stored_before_resume = json.loads(
        (tmp_path / "narrative.json").read_text(encoding="utf-8"))
    assert stored_before_resume["scenes"][0]["participants"][0][
        "actor_id"] == removed_id

    blocked_close = client.post(
        f"/api/cosmere/narrative-scenes/{scene_id}/close", json={
            "expected_revision": 0,
            "request_id": "request.api.scene.close.during-pending-update"})
    assert blocked_close.status_code == 409
    assert blocked_close.get_json()["code"] == "narrative_transaction_pending"

    resumed = client.put(
        f"/api/cosmere/narrative-scenes/{scene_id}", json=update_payload)
    assert resumed.status_code == 200, resumed.get_json()
    assert resumed.get_json()["replayed"] is True
    assert host._load_cosmere_pc(removed_id)["revision"] == removed_revision
    assert host._load_cosmere_pc(added_id)["revision"] == added_revision
    assert not transaction_path.exists()
    stored = json.loads((tmp_path / "narrative.json").read_text(encoding="utf-8"))
    assert stored["scenes"][0]["participants"][0]["actor_id"] == added_id


@pytest.mark.parametrize("scope_id", [None, "scene.other"])
def test_narrative_close_skips_participants_not_scoped_to_that_scene(
        gm_api, scope_id):
    host, client, tmp_path = gm_api
    pid = uuid.uuid4().hex
    host._save_cosmere_pc({
        "id": pid, "system": "cosmere", "name": "Hero", "build": base_build(),
        "runtime_state": {"health": 5}})
    created = client.post("/api/cosmere/narrative-scenes/conversation", json={
        "name": "Scene", "world": "roshar", "era": None,
        "participants": [{"actor_id": pid, "role": "lead"}],
        "tracks": {"disposition": {"value": 0, "target": 3}},
        "expected_store_revision": 0,
        "request_id": f"request.api.scene.close-scope.create.{scope_id}",
    })
    assert created.status_code == 200, created.get_json()
    scene_id = created.get_json()["scene"]["id"]

    def move_out_of_scene(document):
        runtime = deepcopy(document.get("runtime_state") or {})
        runtime["scene"] = {"id": scope_id, "uses": {"recover": 1}}
        runtime["effects"] = [{
            "id": "effect.other.scope", "source_id": "source.other.scope",
            "target_id": pid, "parameters": {}, "selected_attribute": None,
            "stacking_identity": "effect.other.scope.stack",
            "duration_unit": "scene", "remaining_duration": 1,
            "trigger": "scene_end", "refresh_policy": "independent",
            "expiry_state": {}, "expired": False}]
        document["runtime_state"] = runtime
        return document

    current = host._load_cosmere_pc(pid)
    host._mutate_cosmere_pc(pid, current["revision"], move_out_of_scene)
    participant_before = host._load_cosmere_pc(pid)

    closed = client.post(f"/api/cosmere/narrative-scenes/{scene_id}/close", json={
        "expected_revision": 0,
        "request_id": f"request.api.scene.close-scope.close.{scope_id}"})

    assert closed.status_code == 200, closed.get_json()
    assert host._load_cosmere_pc(pid) == participant_before
    stored = json.loads((tmp_path / "narrative.json").read_text(encoding="utf-8"))
    assert stored["scenes"][0]["status"] == "closed"


def test_new_gm_mutations_are_denied_without_gm_authority(gm_api, monkeypatch):
    host, client, _tmp_path = gm_api
    monkeypatch.setattr(host, "_is_gm", lambda: False)
    assert client.post("/api/cosmere/tables/test.table.weather/roll", json={}).status_code == 403
    assert client.post("/api/cosmere/adversaries/custom", json={}).status_code == 403
    assert client.post("/api/cosmere/encounters/stage", json={}).status_code == 403
    assert client.post("/api/cosmere/narrative-scenes/conversation", json={}).status_code == 403
