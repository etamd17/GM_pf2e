"""Actor-scoped Mistborn builder and CAS action API contracts."""
from copy import deepcopy
from types import SimpleNamespace
import uuid

import pytest
from systems.cosmere.rules.paths import registry_path

pytestmark = pytest.mark.skipif(not registry_path().is_file(),
                                reason="private Mistborn rules data is not installed")

from systems.cosmere.rules.profiles import package_pin
from systems.cosmere.rules.profiles import resolve_profile
from systems.cosmere.rules.schema import CosmereRulesBinding, to_dict
from systems.cosmere.rules.seeds import load_seed_registry
from systems.cosmere.mistborn_actions import _compile_effect_execution


IRONPULL = "mistborn.allomancy.iron.action.ironpull"
IRON_BURN = "mistborn.allomancy.iron.action.burn"
STEELPUSH = "mistborn.allomancy.steel.action.steelpush"


def _player_metallic_records():
    registry = load_seed_registry()
    records = []
    for package in registry.pack_versions.values():
        if package.version != "1.1.0" or not package.id.startswith("cosmere.mistborn."):
            continue
        for record in package.records:
            if (record.kind == "action"
                    and record.id.startswith(("mistborn.allomancy.",
                                              "mistborn.feruchemy."))):
                records.append(to_dict(record))
    return sorted(records, key=lambda row: row["id"])


def _audited_pack(registry, pack_id, era=None):
    candidates = [
        pack for pack in registry.pack_versions.values()
        if pack.id == pack_id and pack.version == "1.1.0"
        and (era is None or era in pack.eras)
    ]
    assert len(candidates) == 1
    return candidates[0]


def _binding(era="era-1"):
    registry = load_seed_registry()
    return CosmereRulesBinding(
        package_pin(registry.packs["cosmere.core"]),
        (
            package_pin(_audited_pack(registry, "cosmere.mistborn.core")),
            package_pin(_audited_pack(registry, f"cosmere.mistborn.{era}", era)),
        ),
        "scadrial",
        (era,),
    )


def _misting_build(binding, **changes):
    build = {
        "name": "Iron Misting",
        "level": 1,
        "origin_world": "scadrial",
        "era": "era-1",
        "ancestry": "Human",
        "starting_path": "agent",
        "heroic_paths": ["agent", "misting"],
        "metalborn_keys": ["mistborn.path.key.misting-snap"],
        "attributes": {"str": 2, "spd": 2, "int": 2,
                       "wil": 2, "awa": 2, "pre": 2},
        "skills": {"allomancy": 2},
        "talents": [],
        "expertises": [],
        "epic_choices": [],
        "allomancy_rank": 2,
        "feruchemy_rank": 0,
        "allomantic_powers": {"iron": "nascent"},
        "feruchemical_powers": {},
        "metal_access": {"allomancy": ["iron"], "feruchemy": []},
        "rules_binding": to_dict(binding),
        "cosmere_theme": "mistborn",
    }
    build.update(changes)
    return build


def _ferring_build(binding, **changes):
    build = {
        "name": "Iron Ferring",
        "level": 1,
        "origin_world": "scadrial",
        "era": "era-2",
        "ancestry": "Human",
        "starting_path": "agent",
        "heroic_paths": ["agent", "ferring"],
        "metalborn_keys": ["mistborn.path.key.ferring-heritage"],
        "attributes": {"str": 2, "spd": 2, "int": 2,
                       "wil": 2, "awa": 2, "pre": 2},
        "skills": {"feruchemy": 2},
        "talents": [],
        "expertises": [],
        "epic_choices": [],
        "allomancy_rank": 0,
        "feruchemy_rank": 2,
        "allomantic_powers": {},
        "feruchemical_powers": {"iron": "nascent"},
        "metal_access": {"allomancy": [], "feruchemy": ["iron"]},
        "rules_binding": to_dict(binding),
        "cosmere_theme": "mistborn",
    }
    build.update(changes)
    return build


def _save_metal_actor(host, build, *, current_investiture=3,
                      charge_current=2, rare_access=False,
                      scene_type="freeplay"):
    pid = uuid.uuid4().hex
    document = host._build_cosmere_document(
        {"build": build, "force": True}, None)
    document.update(id=pid, owner_user_id=None)
    runtime = document["runtime_state"]
    runtime["turn"] = {
        "actions_remaining": 3,
        "reaction_available": True,
        "named_actions_used": [],
        "turn_number": 1,
        "choice": "slow",
    }
    runtime["scene"] = {
        "id": f"scene.mistborn.{scene_type}",
        "type": scene_type,
        "uses": {},
    }
    allomancy = runtime["mistborn"]["allomancy"]
    allomancy["current_investiture"] = current_investiture
    if rare_access:
        allomancy["rare_metal_access"] = {
            metal: True for metal in allomancy["accessible_metals"]
        }
    for pool in runtime["mistborn"]["feruchemy"]["by_metal"].values():
        pool.update(charge_current=charge_current, linked_charge_metadata=[])
    document["play_state"] = deepcopy(runtime)
    host._save_cosmere_pc(document)
    return pid, document


@pytest.fixture
def mistborn_api_env(tmp_path, monkeypatch):
    import app as host

    binding = _binding()
    config = {
        "system": "cosmere",
        "cosmere_theme": "mistborn",
        "rules_binding": to_dict(binding),
    }
    character_directory = tmp_path / "cosmere_pcs"
    encounter_directory = tmp_path / "encounters"
    character_directory.mkdir()
    encounter_directory.mkdir()
    monkeypatch.setattr(host, "COSMERE_PC_DIR", str(character_directory))
    monkeypatch.setattr(host, "ENCOUNTER_DIR", str(encounter_directory))
    monkeypatch.setattr(host._storage, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(host, "ACTIVE_CAMPAIGN_ID", None)
    monkeypatch.setattr(host, "_load_campaign_config", lambda: deepcopy(config))
    monkeypatch.setattr(host, "_is_gm", lambda: True)

    pid = uuid.uuid4().hex
    document = host._build_cosmere_document(
        {"build": _misting_build(binding), "force": True}, None)
    document.update(id=pid, owner_user_id=None)
    runtime = document["runtime_state"]
    runtime["turn"] = {
        "actions_remaining": 3,
        "reaction_available": True,
        "named_actions_used": [],
        "turn_number": 1,
        "choice": "slow",
    }
    runtime["scene"] = {"id": "scene.mistborn", "uses": {}}
    runtime["mistborn"]["allomancy"]["current_investiture"] = 3
    document["play_state"] = deepcopy(runtime)
    host._save_cosmere_pc(document)

    yield host, host.app.test_client(), pid, binding
    host.ACTIVE_ENCOUNTER[:] = []


@pytest.fixture
def ferring_api_env(tmp_path, monkeypatch):
    import app as host

    binding = _binding("era-2")
    config = {
        "system": "cosmere",
        "cosmere_theme": "mistborn",
        "rules_binding": to_dict(binding),
    }
    character_directory = tmp_path / "cosmere_pcs"
    encounter_directory = tmp_path / "encounters"
    character_directory.mkdir()
    encounter_directory.mkdir()
    monkeypatch.setattr(host, "COSMERE_PC_DIR", str(character_directory))
    monkeypatch.setattr(host, "ENCOUNTER_DIR", str(encounter_directory))
    monkeypatch.setattr(host._storage, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(host, "ACTIVE_CAMPAIGN_ID", None)
    monkeypatch.setattr(host, "_load_campaign_config", lambda: deepcopy(config))
    monkeypatch.setattr(host, "_is_gm", lambda: True)

    pid = uuid.uuid4().hex
    document = host._build_cosmere_document(
        {"build": _ferring_build(binding), "force": True}, None)
    document.update(id=pid, owner_user_id=None)
    runtime = document["runtime_state"]
    runtime["turn"] = {
        "actions_remaining": 3,
        "reaction_available": True,
        "named_actions_used": [],
        "turn_number": 1,
        "choice": "slow",
    }
    runtime["scene"] = {
        "id": "scene.mistborn.conversation",
        "type": "conversation",
        "uses": {},
    }
    runtime["mistborn"]["feruchemy"]["by_metal"]["iron"].update(
        charge_current=2, linked_charge_metadata=[])
    document["play_state"] = deepcopy(runtime)
    host._save_cosmere_pc(document)

    yield host, host.app.test_client(), pid, binding
    host.ACTIVE_ENCOUNTER[:] = []


def test_api_builder_preview_alias_is_profile_neutral(mistborn_api_env):
    _host, client, _pid, binding = mistborn_api_env
    response = client.post(
        "/api/cosmere/builder/preview",
        json={"build": _misting_build(binding)},
    )

    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["origin_world"] == "scadrial"
    assert body["era"] == "era-1"
    assert body["is_radiant"] is False
    assert body["derived"]["metallic_arts"]["allomancy"] == {
        "limit": 2, "die": "d6", "range_ft": 40,
    }
    assert body["rules_binding"] == to_dict(binding)


def test_builder_creation_does_not_mint_client_goal_authority(mistborn_api_env):
    host, client, _pid, binding = mistborn_api_env
    forged = _misting_build(binding)
    forged["goal_entitlements"] = [
        "mistborn.entitlement.reward.pure-lerasium",
    ]

    response = client.post("/cosmere/builder", json={
        "build": forged,
        # A GM may force an incomplete build, but force cannot mint authority.
        "force": True,
    })

    assert response.status_code == 200, response.get_json()
    stored = host._load_cosmere_pc(response.get_json()["id"])
    assert stored["origin_world"] == "scadrial"
    assert stored["era"] == "era-1"
    assert stored["build"]["goal_entitlements"] == []
    assert stored["rules_binding"] == to_dict(binding)


def test_mistborn_action_uses_cas_and_records_exact_server_pins(
        mistborn_api_env, monkeypatch):
    host, client, pid, binding = mistborn_api_env
    order = []
    real_save = host._save_cosmere_pc

    def save(*args, **kwargs):
        result = real_save(*args, **kwargs)
        order.append("save")
        return result

    monkeypatch.setattr(host, "_save_cosmere_pc", save)
    monkeypatch.setattr(
        host, "_sync_cosmere_combatant_state",
        lambda actor_id, state, character_revision=None:
        order.append("sync") or True,
    )
    monkeypatch.setattr(
        host, "_do_persist_encounter_state_unlocked",
        lambda: order.append("autosave") or True,
    )
    monkeypatch.setattr(
        host, "sse_broadcast",
        lambda event, *args, **kwargs: order.append(event),
    )

    response = client.post(f"/api/cosmere/pc/{pid}/actions/{IRONPULL}", json={
        "expected_revision": 0,
        "request_id": "mistborn.action.ironpull.once",
        "operation": {"kind": "spend", "amount": 1},
    })

    assert response.status_code == 200, response.get_json()
    assert order == [
        "save", "sync", "autosave", "encounter_update",
        "cosmere_player_state",
    ]
    body = response.get_json()
    receipt = body["receipt"]
    assert body["revision"] == 1
    assert receipt["action_id"] == IRONPULL
    mistborn_receipt = receipt["mistborn"]
    expected_mistborn = {
        "actor_id": pid,
        "art": "allomancy",
        "metal": "iron",
        "rank": 2,
        "era": "era-1",
        "scene_type": "freeplay",
        "operation": {"kind": "spend", "metal": "iron", "amount": 1},
        "effect": None,
    }
    assert {key: mistborn_receipt[key] for key in expected_mistborn} == \
        expected_mistborn
    assert mistborn_receipt["resource_cost"] == {
        "mode": "selected", "amount": 1,
    }
    assert mistborn_receipt["execution"]["declared_disposition"] == "tracked"
    assert mistborn_receipt["execution"]["disposition"] == "tracked"
    assert mistborn_receipt["execution"]["applied_effects"] == []
    assert mistborn_receipt["execution"]["unresolved_effects"]
    assert receipt["rules_binding"] == to_dict(binding)
    expected_package = next(
        to_dict(pin) for pin in binding.enabled_packs
        if pin.id == "cosmere.mistborn.core"
    )
    assert {
        key: receipt["package"][key]
        for key in ("id", "version", "package_hash")
    } == expected_package
    assert receipt["package"]["dependencies"] == ["cosmere.core"]
    assert receipt["package_pins"] == [
        to_dict(binding.core_package),
        *[to_dict(pin) for pin in binding.enabled_packs],
    ]
    stored = host._load_cosmere_pc(pid)
    assert stored["runtime_state"]["turn"]["actions_remaining"] == 2
    assert stored["runtime_state"]["mistborn"]["allomancy"][
        "current_investiture"] == 2


def test_mistborn_action_preserves_replay_and_stale_revision_semantics(
        mistborn_api_env):
    host, client, pid, _binding_value = mistborn_api_env
    payload = {
        "expected_revision": 0,
        "request_id": "mistborn.action.retry.once",
        "operation": {"kind": "spend", "amount": 1},
    }

    first = client.post(
        f"/api/cosmere/pc/{pid}/actions/{IRONPULL}", json=payload)
    replay = client.post(
        f"/api/cosmere/pc/{pid}/actions/{IRONPULL}", json=payload)
    stale = client.post(
        f"/api/cosmere/pc/{pid}/actions/{IRONPULL}", json={
            **payload,
            "request_id": "mistborn.action.stale.once",
        })

    assert first.status_code == 200, first.get_json()
    assert replay.status_code == 200
    assert replay.get_json()["replayed"] is True
    assert replay.get_json()["receipt"] == first.get_json()["receipt"]
    assert stale.status_code == 409
    assert stale.get_json()["code"] == "stale_revision"
    assert host._load_cosmere_pc(pid)["revision"] == 1


@pytest.mark.parametrize("spoof", [
    {"art": "feruchemy"},
    {"metal": "steel"},
    {"rank": 6},
    {"era": "era-2"},
    {"package": {"id": "cosmere.mistborn.core", "version": "999.0.0"}},
    {"operation": {"kind": "spend", "amount": 1, "metal": "steel"}},
    {"operation": {"kind": "spend", "amount": 1, "rank": 6}},
    {"effects": []},
    {"operation": {"kind": "spend", "amount": 1, "effect": {}}},
])
def test_mistborn_action_rejects_client_authority_spoof_without_mutation(
        mistborn_api_env, spoof):
    host, client, pid, _binding_value = mistborn_api_env
    payload = {
        "expected_revision": 0,
        "request_id": "mistborn.action.spoof.once",
        "operation": {"kind": "spend", "amount": 1},
        **spoof,
    }

    response = client.post(
        f"/api/cosmere/pc/{pid}/actions/{IRONPULL}", json=payload)

    assert response.status_code == 422, response.get_json()
    assert response.get_json()["code"] == "mistborn_action_spoof"
    stored = host._load_cosmere_pc(pid)
    assert stored["revision"] == 0
    assert stored["runtime_state"]["mistborn"]["allomancy"][
        "current_investiture"] == 3


def test_mistborn_action_catalog_is_scoped_to_actor_metal_access(
        mistborn_api_env):
    host, client, pid, _binding_value = mistborn_api_env
    response = client.post(
        f"/api/cosmere/pc/{pid}/actions/{STEELPUSH}", json={
            "expected_revision": 0,
            "request_id": "mistborn.action.steel.denied",
            "operation": {"kind": "spend", "amount": 1},
        })

    assert response.status_code == 409, response.get_json()
    assert response.get_json()["code"] == "metal_unavailable"
    assert host._load_cosmere_pc(pid)["revision"] == 0


@pytest.mark.parametrize(
    ("metal", "expected_investiture"),
    [("iron", 3), ("aluminum", 0), ("duralumin", 0)],
)
def test_allomancy_action_uses_exact_catalog_resource_cost(
        mistborn_api_env, metal, expected_investiture):
    host, client, _pid, binding = mistborn_api_env
    build = _misting_build(
        binding,
        allomantic_powers={metal: "nascent"},
        metal_access={"allomancy": [metal], "feruchemy": []},
    )
    pid, document = _save_metal_actor(
        host, build, current_investiture=3,
        rare_access=metal in {"aluminum", "duralumin"})
    action_id = f"mistborn.allomancy.{metal}.action.burn"

    response = client.post(f"/api/cosmere/pc/{pid}/actions/{action_id}", json={
        "expected_revision": document["revision"],
        "request_id": f"mistborn.action.cost.{metal}",
        "operation": {"kind": "spend", "amount": 1},
    })

    assert response.status_code == 200, response.get_json()
    receipt = response.get_json()["receipt"]
    assert receipt["mistborn"]["resource_cost"]["mode"] == (
        "none" if metal == "iron" else "all_remaining")
    assert host._load_cosmere_pc(pid)["runtime_state"]["mistborn"][
        "allomancy"]["current_investiture"] == expected_investiture


def test_allomancy_spend_requires_server_persisted_rare_metal_access(
        mistborn_api_env):
    host, client, _pid, binding = mistborn_api_env
    build = _misting_build(
        binding,
        allomantic_powers={"gold": "nascent"},
        metal_access={"allomancy": ["gold"], "feruchemy": []},
    )
    pid, document = _save_metal_actor(host, build, current_investiture=3)

    response = client.post(
        f"/api/cosmere/pc/{pid}/actions/mistborn.allomancy.gold.action.burn",
        json={
            "expected_revision": document["revision"],
            "request_id": "mistborn.action.rare.gold.denied",
            "operation": {"kind": "spend", "amount": 1},
        },
    )

    assert response.status_code == 409, response.get_json()
    assert response.get_json()["code"] == "rare_metal_unavailable"
    stored = host._load_cosmere_pc(pid)
    assert stored["revision"] == document["revision"]
    assert stored["runtime_state"]["mistborn"]["allomancy"][
        "current_investiture"] == 3


def test_server_loaded_action_build_preserves_persisted_advancement_grants(
        mistborn_api_env):
    host, _client, pid, _binding_value = mistborn_api_env
    from systems.cosmere.mistborn_actions import _trusted_build

    document = host._load_cosmere_pc(pid)
    document["build"]["advancement_bonus_grants"] = {
        "attribute_points": 2,
        "skill_ranks": 1,
        "talents": 1,
        "expertises": 0,
    }
    profile = resolve_profile(document)

    build = _trusted_build(profile, document)

    assert build.advancement_bonus_grants == {
        "attribute_points": 2,
        "skill_ranks": 1,
        "talents": 1,
        "expertises": 0,
    }


def test_feruchemy_action_derives_context_and_materializes_shared_duration(
        ferring_api_env):
    host, client, pid, binding = ferring_api_env
    action_id = "mistborn.feruchemy.iron.action.tap-weight"

    response = client.post(f"/api/cosmere/pc/{pid}/actions/{action_id}", json={
        "expected_revision": 0,
        "request_id": "mistborn.action.tap-weight.once",
        "operation": {"kind": "tap_start", "amount": 1},
    })

    assert response.status_code == 200, response.get_json()
    receipt = response.get_json()["receipt"]
    assert receipt["rules_binding"] == to_dict(binding)
    assert receipt["mistborn"]["actor_id"] == pid
    assert receipt["mistborn"]["art"] == "feruchemy"
    assert receipt["mistborn"]["metal"] == "iron"
    assert receipt["mistborn"]["rank"] == 2
    assert receipt["mistborn"]["era"] == "era-2"
    assert receipt["mistborn"]["scene_type"] == "conversation"
    effect = receipt["mistborn"]["effect"]
    assert effect["source_id"] == action_id
    assert effect["target_id"] == pid
    assert effect["duration_unit"] == "contribution"
    assert effect["remaining_duration"] == 2
    assert effect["trigger"] == "contribution_end"
    assert effect["expiry_state"] == {"actor_id": pid}

    stored = host._load_cosmere_pc(pid)["runtime_state"]
    assert stored["mistborn"]["feruchemy"]["by_metal"]["iron"][
        "charge_current"] == 1
    assert stored["mistborn"]["feruchemy"]["by_metal"]["iron"][
        "mode"] == "tapping"
    assert stored["effects"] == [effect]
    assert stored["mistborn"]["feruchemy"]["by_metal"]["iron"][
        "active_effects"] == [effect]


def test_allomancy_action_materializes_canonical_and_nested_burn(
        mistborn_api_env):
    host, client, pid, _binding_value = mistborn_api_env
    document = host._load_cosmere_pc(pid)
    document["runtime_state"]["scene"]["type"] = "conversation"
    document["play_state"] = deepcopy(document["runtime_state"])
    host._save_cosmere_pc(document)
    expected_revision = document["revision"]

    response = client.post(f"/api/cosmere/pc/{pid}/actions/{IRON_BURN}", json={
        "expected_revision": expected_revision,
        "request_id": "mistborn.action.burn-iron.once",
        "operation": {"kind": "spend", "amount": 1},
    })

    assert response.status_code == 200, response.get_json()
    effect = response.get_json()["receipt"]["mistborn"]["effect"]
    assert effect["duration_unit"] == "contribution"
    assert effect["remaining_duration"] == 1
    stored = host._load_cosmere_pc(pid)["runtime_state"]
    assert stored["effects"] == [effect]
    assert stored["mistborn"]["allomancy"]["active_burns"] == [effect]


def test_calculated_effect_ops_compile_to_typed_projectable_modifiers(
        mistborn_api_env):
    host, client, _pid, binding = mistborn_api_env
    build = _misting_build(
        binding,
        allomantic_powers={"pewter": "nascent"},
        metal_access={"allomancy": ["pewter"], "feruchemy": []},
    )
    pid, document = _save_metal_actor(host, build, current_investiture=3)

    response = client.post(
        f"/api/cosmere/pc/{pid}/actions/mistborn.allomancy.pewter.action.burn",
        json={
            "expected_revision": document["revision"],
            "request_id": "mistborn.action.pewter.typed",
            "operation": {"kind": "spend", "amount": 2},
        },
    )

    assert response.status_code == 200, response.get_json()
    receipt = response.get_json()["receipt"]
    assert receipt["mistborn"]["execution"]["disposition"] == "calculated"
    assert receipt["mistborn"]["execution"]["unresolved_effects"] == []
    effect = receipt["mistborn"]["effect"]
    modifiers = effect["parameters"]["applied_effects"]
    assert [(row["kind"], row["stat"], row.get("amount"))
            for row in modifiers] == [
        ("modifier", "speed", 1),
        ("modifier", "strength", 1),
        ("penalty_override", "exhausted_test_penalty", 0),
    ]
    assert all(row["source_id"] == effect["source_id"] for row in modifiers)
    assert all(row["target_id"] == pid for row in modifiers)
    assert all(row["stacking_identity"].startswith(
        "mistborn.allomancy.pewter") for row in modifiers)

    stored = host._load_cosmere_pc(pid)["runtime_state"]
    profile = resolve_profile(host._load_cosmere_pc(pid))
    projected = profile.project_actor(build, runtime_state=stored)
    projected_modifiers = projected["active_effect_modifiers"]
    assert [(row["stat"], row.get("amount")) for row in projected_modifiers] == [
        ("speed", 1),
        ("strength", 1),
        ("exhausted_test_penalty", 0),
    ]
    assert all(row["duration_unit"] == "minute" for row in projected_modifiers)


@pytest.mark.parametrize(
    "record",
    _player_metallic_records(),
    ids=lambda record: record["id"],
)
def test_calculated_player_metallic_actions_have_complete_typed_execution(record):
    mechanics = record["parameters"]["data"]
    execution = _compile_effect_execution(
        record,
        mechanics,
        {"kind": "spend", "amount": 1},
        actor_id="actor.audit",
        rank=2,
    )

    if record["automation"] != "calculated":
        return
    assert execution["disposition"] == "calculated"
    assert execution["unresolved_effects"] == []
    if mechanics.get("effect_ops"):
        assert execution["applied_effects"]


def test_store_duration_uses_requested_charge_count(ferring_api_env):
    host, client, pid, _binding_value = ferring_api_env
    action_id = "mistborn.feruchemy.iron.action.store-weight"

    response = client.post(f"/api/cosmere/pc/{pid}/actions/{action_id}", json={
        "expected_revision": 0,
        "request_id": "mistborn.action.store-weight.two-scenes",
        "operation": {"kind": "store_start", "amount": 2},
    })

    assert response.status_code == 200, response.get_json()
    effect = response.get_json()["receipt"]["mistborn"]["effect"]
    assert effect["duration_unit"] == "scene"
    assert effect["remaining_duration"] == 2
    assert effect["parameters"]["charge_count"] == 2
    stored = host._load_cosmere_pc(pid)["runtime_state"]
    assert stored["mistborn"]["feruchemy"]["by_metal"]["iron"][
        "mode"] == "storing"
    assert stored["effects"] == [effect]


def test_tap_nutrition_materializes_one_day_sustenance_effect(ferring_api_env):
    host, client, _pid, binding = ferring_api_env
    build = _ferring_build(
        binding,
        feruchemical_powers={"bendalloy": "nascent"},
        metal_access={"allomancy": [], "feruchemy": ["bendalloy"]},
    )
    pid, document = _save_metal_actor(host, build, charge_current=2)
    action_id = "mistborn.feruchemy.bendalloy.action.tap-nutrition"

    response = client.post(f"/api/cosmere/pc/{pid}/actions/{action_id}", json={
        "expected_revision": document["revision"],
        "request_id": "mistborn.action.tap-nutrition.day",
        "operation": {"kind": "tap_start", "amount": 1},
    })

    assert response.status_code == 200, response.get_json()
    effect = response.get_json()["receipt"]["mistborn"]["effect"]
    assert effect["duration_unit"] == "hour"
    assert effect["remaining_duration"] == 24
    assert effect["trigger"] == "manual"
    assert [(row["kind"], row["stat"], row.get("amount"))
            for row in effect["parameters"]["applied_effects"]] == [
        ("status", "sustenance", None),
        ("modifier", "exhausted_penalty", -2),
    ]
    stored = host._load_cosmere_pc(pid)["runtime_state"]
    assert stored["mistborn"]["feruchemy"]["by_metal"]["bendalloy"][
        "charge_current"] == 1
    assert stored["effects"] == [effect]


@pytest.mark.parametrize("scene_type", ["conversation", "endeavor"])
def test_narrative_action_advances_only_actor_contribution_once_and_publishes_complete(
        mistborn_api_env, monkeypatch, scene_type):
    host, client, pid, _binding_value = mistborn_api_env
    other_id = uuid.uuid4().hex
    document = host._load_cosmere_pc(pid)
    document["runtime_state"]["scene"]["type"] = scene_type

    def contribution_effect(effect_id, actor_id):
        return {
            "id": effect_id,
            "source_id": "mistborn.effect.contribution-test",
            "target_id": actor_id,
            "parameters": {},
            "selected_attribute": None,
            "stacking_identity": f"{effect_id}.stack",
            "duration_unit": "contribution",
            "remaining_duration": 2,
            "trigger": "contribution_end",
            "refresh_policy": "independent",
            "expiry_state": {"actor_id": actor_id},
            "expired": False,
        }

    effects = [
        contribution_effect("effect.contribution.actor", pid),
        contribution_effect("effect.contribution.other", other_id),
    ]
    document["runtime_state"]["effects"] = deepcopy(effects)
    document["runtime_state"]["mistborn"]["allomancy"][
        "active_burns"] = deepcopy(effects)
    document["play_state"] = deepcopy(document["runtime_state"])
    host._save_cosmere_pc(document)
    published = []
    monkeypatch.setattr(
        host, "sse_broadcast",
        lambda event, payload, *args, **kwargs:
        published.append((event, deepcopy(payload))),
    )
    payload = {
        "expected_revision": document["revision"],
        "request_id": f"mistborn.action.contribution.{scene_type}",
        "operation": {"kind": "spend", "amount": 1},
    }

    first = client.post(
        f"/api/cosmere/pc/{pid}/actions/{IRONPULL}", json=payload)
    replay = client.post(
        f"/api/cosmere/pc/{pid}/actions/{IRONPULL}", json=payload)

    assert first.status_code == 200, first.get_json()
    assert replay.status_code == 200 and replay.get_json()["replayed"] is True
    stored = host._load_cosmere_pc(pid)["runtime_state"]
    assert [row["remaining_duration"] for row in stored["effects"]] == [1, 2]
    assert stored["mistborn"]["allomancy"]["active_burns"] == stored["effects"]
    contribution = first.get_json()["receipt"]["contribution"]
    assert contribution["trigger"] == "contribution_end"
    assert contribution["actor_id"] == pid
    assert contribution["scene_type"] == scene_type
    assert contribution["expired"] == []
    assert len(published) == 1
    assert published[0][0] == "cosmere_player_state"
    assert published[0][1]["action_receipt"][
        "coordination_status"] == "complete"


def test_refreshed_effect_receipt_uses_the_canonical_persisted_instance(
        mistborn_api_env):
    host, _client, pid, _binding_value = mistborn_api_env
    from systems.cosmere.mistborn_actions import (
        apply_mistborn_action_transaction,
        compile_mistborn_action,
    )

    document = host._load_cosmere_pc(pid)
    runtime = deepcopy(document["runtime_state"])
    runtime["scene"]["type"] = "conversation"
    profile = resolve_profile(document)
    first_command = compile_mistborn_action(
        profile, document, IRON_BURN, {
            "request_id": "mistborn.action.burn.refresh.first",
            "expected_revision": 0,
            "operation": {"kind": "spend", "amount": 1},
        }, runtime_state=runtime)
    first_state, first_receipt = apply_mistborn_action_transaction(
        runtime, first_command, profile=profile,
        request_id="mistborn.action.burn.refresh.first")
    first_effect_id = first_receipt["mistborn"]["effect"]["id"]

    first_state["turn"].update(
        actions_remaining=3, named_actions_used=[], turn_number=2)
    second_command = compile_mistborn_action(
        profile, document, IRON_BURN, {
            "request_id": "mistborn.action.burn.refresh.second",
            "expected_revision": 1,
            "operation": {"kind": "spend", "amount": 1},
        }, runtime_state=first_state)
    second_state, second_receipt = apply_mistborn_action_transaction(
        first_state, second_command, profile=profile,
        request_id="mistborn.action.burn.refresh.second")

    assert len(second_state["effects"]) == 1
    assert second_state["effects"][0]["id"] == first_effect_id
    assert second_receipt["mistborn"]["effect"] == second_state["effects"][0]
    assert second_state["mistborn"]["allomancy"]["active_burns"] == \
        second_state["effects"]


def test_combat_duration_uses_server_live_phase_and_round(
        mistborn_api_env, monkeypatch):
    host, client, pid, _binding_value = mistborn_api_env
    document = host._load_cosmere_pc(pid)
    document["runtime_state"]["scene"]["type"] = "combat"
    document["play_state"] = deepcopy(document["runtime_state"])
    host._save_cosmere_pc(document)
    live_actor = SimpleNamespace(
        system="cosmere", is_pc=True, restore_id=pid,
        turn_serial=1, max_actions=2, actions_used=0,
        reaction_used=False, speed_choice="fast",
    )
    host.ACTIVE_ENCOUNTER[:] = [live_actor]
    monkeypatch.setattr(host, "ROUND_NUMBER", 4)
    monkeypatch.setattr(host, "_sync_cosmere_combatant_state", lambda *args, **kwargs: False)
    monkeypatch.setattr(host, "sse_broadcast", lambda *args, **kwargs: None)

    response = client.post(f"/api/cosmere/pc/{pid}/actions/{IRON_BURN}", json={
        "expected_revision": document["revision"],
        "request_id": "mistborn.action.burn.combat-phase",
        "operation": {"kind": "spend", "amount": 1},
    })

    assert response.status_code == 200, response.get_json()
    effect = response.get_json()["receipt"]["mistborn"]["effect"]
    assert effect["duration_unit"] == "round"
    assert effect["trigger"] == "phase_start"
    assert effect["remaining_duration"] == 1
    assert effect["expiry_state"] == {
        "phase": "fast_pc", "not_before_round": 5,
    }


@pytest.mark.parametrize(("action_name", "start", "stop"), [
    ("store-weight", "store_start", "store_stop"),
    ("tap-weight", "tap_start", "tap_stop"),
])
def test_stopping_a_metalmind_is_free_in_the_same_turn(
        ferring_api_env, action_name, start, stop):
    # User ruling 2026-10-28: a stop spends no action and does not use, or
    # need, the action's once-per-turn slot. Starting again is still limited.
    host, client, pid, _binding_value = ferring_api_env
    action_id = f"mistborn.feruchemy.iron.action.{action_name}"
    started = client.post(f"/api/cosmere/pc/{pid}/actions/{action_id}", json={
        "expected_revision": 0, "request_id": str(uuid.uuid4()),
        "operation": {"kind": start, "amount": 1}})
    assert started.status_code == 200, started.get_json()
    after_start = host._load_cosmere_pc(pid)
    remaining = after_start["runtime_state"]["turn"]["actions_remaining"]

    stopped = client.post(f"/api/cosmere/pc/{pid}/actions/{action_id}", json={
        "expected_revision": after_start["revision"],
        "request_id": str(uuid.uuid4()),
        "operation": {"kind": stop}})
    assert stopped.status_code == 200, stopped.get_json()
    after_stop = host._load_cosmere_pc(pid)
    turn = after_stop["runtime_state"]["turn"]
    assert turn["actions_remaining"] == remaining
    assert after_stop["runtime_state"]["mistborn"]["feruchemy"]["by_metal"]["iron"][
        "mode"] == "idle"

    again = client.post(f"/api/cosmere/pc/{pid}/actions/{action_id}", json={
        "expected_revision": after_stop["revision"],
        "request_id": str(uuid.uuid4()),
        "operation": {"kind": start, "amount": 1}})
    assert again.status_code == 409
    assert again.get_json()["code"] == "named_action_used"
