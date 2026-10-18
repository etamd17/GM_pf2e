from copy import deepcopy

import pytest

from systems.cosmere.mistborn_runtime import (
    ALLOMANCY_OPERATION_KINDS,
    FERUCHEMY_OPERATION_KINDS,
    MistbornRuntimeError,
    allomancy_maximum,
    apply_allomancy_operation,
    apply_feruchemy_operation,
    feruchemy_maximum,
    governing_attribute,
    metallic_art_scaling,
    normalize_allomancy_state,
    normalize_feruchemy_state,
    normalize_mistborn_runtime,
)


ATTRIBUTES = {
    "strength": 2,
    "speed": 3,
    "intellect": 4,
    "willpower": 5,
    "awareness": 2,
    "presence": 4,
}


def allomancy(raw=None, *, metals=("iron", "steel"), prior=()):
    return normalize_allomancy_state(
        raw or {}, attributes=ATTRIBUTES, rank=3, era="era-1",
        accessible_metals=metals, prior_accessible_metals=prior,
    )


def feruchemy(raw=None, *, metals=("iron", "steel"), prior=()):
    return normalize_feruchemy_state(
        raw or {}, rank=3, era="era-1", accessible_metals=metals,
        prior_accessible_metals=prior,
    )


@pytest.mark.parametrize(
    ("rank", "expected"),
    [
        (0, {"rank": 0, "limit": 1, "die": None, "range_ft": 10}),
        (1, {"rank": 1, "limit": 1, "die": "d4", "range_ft": 20}),
        (2, {"rank": 2, "limit": 2, "die": "d6", "range_ft": 40}),
        (3, {"rank": 3, "limit": 3, "die": "d8", "range_ft": 80}),
        (4, {"rank": 4, "limit": 4, "die": "d10", "range_ft": 160}),
        (5, {"rank": 5, "limit": 5, "die": "d12", "range_ft": 320}),
        (6, {"rank": 6, "limit": 6, "die": "d20", "range_ft": 640}),
        (9, {"rank": 9, "limit": 9, "die": "d20", "range_ft": 640}),
    ],
)
def test_metallic_art_rank_table_is_literal_and_caps_only_range(rank, expected):
    assert metallic_art_scaling(rank) == expected


@pytest.mark.parametrize("rank", [-1, True, 1.5, "3"])
def test_metallic_art_rank_rejects_invalid_values(rank):
    with pytest.raises(MistbornRuntimeError, match="rank") as caught:
        metallic_art_scaling(rank)
    assert caught.value.code == "invalid_rank"


def test_governing_attributes_and_pool_maxima_are_authoritative():
    assert governing_attribute("allomancy") == "willpower"
    assert governing_attribute("feruchemy") == "intellect"
    assert allomancy_maximum(ATTRIBUTES) == 6
    assert feruchemy_maximum(3) == 5
    with pytest.raises(MistbornRuntimeError) as caught:
        governing_attribute("surgebinding")
    assert caught.value.code == "invalid_art"


def test_allomancy_normalization_preserves_explicit_zero_and_recomputes_maximum():
    state = allomancy({
        "current_investiture": 0,
        "max_investiture": 999,
        "depleted_metals": ["iron"],
        "residual_by_power": {"iron": 2},
    })
    assert state["governing_attribute"] == "willpower"
    assert state["max_investiture"] == 6
    assert state["current_investiture"] == 0
    assert state["depleted_metals"] == ["iron"]
    assert state["residual_by_power"] == {"iron": 2}


def test_scene_start_full_or_one_when_surprised_without_mutating_input():
    original = allomancy({"current_investiture": 0})
    before = deepcopy(original)
    normal = apply_allomancy_operation(original, {"kind": "scene_start", "surprised": False})
    surprised = apply_allomancy_operation(original, {"kind": "scene_start", "surprised": True})
    assert original == before
    assert normal["current_investiture"] == 6
    assert surprised["current_investiture"] == 1


def test_allomancy_spending_is_shared_but_depletion_is_independent_and_pure():
    original = allomancy({"current_investiture": 5})
    spent = apply_allomancy_operation(
        original, {"kind": "spend", "metal": "iron", "amount": 2})
    depleted = apply_allomancy_operation(
        spent, {"kind": "deplete", "metal": "iron"})

    assert original["current_investiture"] == 5
    assert spent["current_investiture"] == 3
    assert spent["depleted_metals"] == []
    assert depleted["depleted_metals"] == ["iron"]
    assert apply_allomancy_operation(
        spent, {"kind": "deplete", "metal": "iron"}) == depleted
    assert "steel" not in depleted["depleted_metals"]

    with pytest.raises(MistbornRuntimeError) as caught:
        apply_allomancy_operation(
            depleted, {"kind": "spend", "metal": "iron", "amount": 1})
    assert caught.value.code == "metal_depleted"


def test_allomancy_spend_enforces_rank_limit_resources_and_closed_operations():
    state = allomancy({"current_investiture": 2})
    cases = [
        ({"kind": "spend", "metal": "iron", "amount": 4}, "limit_exceeded"),
        ({"kind": "spend", "metal": "iron", "amount": 3}, "insufficient_investiture"),
        ({"kind": "spend", "metal": "tin", "amount": 1}, "metal_unavailable"),
        ({"kind": "spend", "metal": "wood", "amount": 1}, "invalid_metal"),
        ({"kind": "burn", "metal": "iron", "amount": 1}, "invalid_operation"),
        ({"kind": "spend", "metal": "iron", "amount": 1, "client_max": 9},
         "invalid_operation_fields"),
    ]
    for operation, code in cases:
        with pytest.raises(MistbornRuntimeError) as caught:
            apply_allomancy_operation(state, operation)
        assert caught.value.code == code
    assert ALLOMANCY_OPERATION_KINDS == frozenset(
        {"scene_start", "spend", "deplete", "drink_vial", "restore"})


def test_drinking_vial_consumes_inventory_restores_shared_pool_and_updates_each_metal():
    state = allomancy({
        "current_investiture": 0,
        "depleted_metals": ["iron", "steel"],
        "vial_inventory": {
            "vial.iron": {"count": 2, "metals": ["iron"]},
        },
    })
    result = apply_allomancy_operation(
        state, {"kind": "drink_vial", "vial_id": "vial.iron"})
    assert result["current_investiture"] == result["max_investiture"] == 6
    assert result["depleted_metals"] == ["steel"]
    assert result["vial_inventory"]["vial.iron"]["count"] == 1
    assert state["vial_inventory"]["vial.iron"]["count"] == 2


def test_rare_vial_access_is_per_metal_and_does_not_leak_between_metals():
    state = normalize_allomancy_state({
        "current_investiture": 0,
        "rare_metal_access": {"gold": True, "electrum": False},
        "vial_inventory": {
            "vial.gold": {"count": 1, "metals": ["gold"]},
            "vial.electrum": {"count": 1, "metals": ["electrum"]},
        },
    }, attributes=ATTRIBUTES, rank=3, era="era-1",
       accessible_metals=("gold", "electrum"))

    gold = apply_allomancy_operation(
        state, {"kind": "drink_vial", "vial_id": "vial.gold"})
    assert gold["depleted_metals"] == ["electrum"]
    with pytest.raises(MistbornRuntimeError) as caught:
        apply_allomancy_operation(
            state, {"kind": "drink_vial", "vial_id": "vial.electrum"})
    assert caught.value.code == "rare_metal_unavailable"


def test_restore_is_capped_and_does_not_silently_clear_depletion():
    state = allomancy({"current_investiture": 0, "depleted_metals": ["iron"]})
    partial = apply_allomancy_operation(state, {"kind": "restore", "amount": 2})
    full = apply_allomancy_operation(partial, {"kind": "restore"})
    assert partial["current_investiture"] == 2
    assert full["current_investiture"] == 6
    assert full["depleted_metals"] == ["iron"]


def test_feruchemy_has_independent_clamped_pools_and_no_investiture_pool():
    state = feruchemy({
        "investiture": 8,
        "current_investiture": 8,
        "by_metal": {
            "iron": {"charge_current": 99, "mode": "idle"},
            "steel": {"charge_current": -4, "mode": "idle"},
        },
    })
    assert state["governing_attribute"] == "intellect"
    assert state["by_metal"]["iron"]["charge_current"] == 5
    assert state["by_metal"]["steel"]["charge_current"] == 0
    assert all("investiture" not in key for key in state)


def test_feruchemy_store_and_tap_are_mutually_exclusive_with_explicit_maintenance():
    state = feruchemy({"by_metal": {"iron": {"charge_current": 2}}})
    storing = apply_feruchemy_operation(
        state, {"kind": "store_start", "metal": "iron"})
    assert storing["by_metal"]["iron"]["mode"] == "storing"
    assert storing["by_metal"]["iron"]["charge_current"] == 2

    stored = apply_feruchemy_operation(
        storing, {"kind": "store_maintain", "metal": "iron", "amount": 2})
    assert stored["by_metal"]["iron"]["charge_current"] == 4

    tapping = apply_feruchemy_operation(
        stored, {"kind": "tap_start", "metal": "iron", "amount": 1})
    assert tapping["by_metal"]["iron"]["mode"] == "tapping"
    assert tapping["by_metal"]["iron"]["charge_current"] == 3

    maintained = apply_feruchemy_operation(
        tapping, {"kind": "tap_maintain", "metal": "iron", "amount": 2})
    assert maintained["by_metal"]["iron"]["charge_current"] == 1
    stopped = apply_feruchemy_operation(
        maintained, {"kind": "tap_stop", "metal": "iron"})
    assert stopped["by_metal"]["iron"]["mode"] == "idle"
    assert state["by_metal"]["iron"]["charge_current"] == 2


def test_feruchemy_operation_guards_modes_limits_resources_and_unknown_fields():
    state = feruchemy({"by_metal": {"iron": {"charge_current": 2}}})
    cases = [
        ({"kind": "store_maintain", "metal": "iron", "amount": 1}, "wrong_mode"),
        ({"kind": "tap_maintain", "metal": "iron", "amount": 1}, "wrong_mode"),
        ({"kind": "tap_start", "metal": "iron", "amount": 3}, "insufficient_charges"),
        ({"kind": "tap_start", "metal": "iron", "amount": 4}, "limit_exceeded"),
        ({"kind": "tap_start", "metal": "tin", "amount": 1}, "metal_unavailable"),
        ({"kind": "tap_start", "metal": "wood", "amount": 1}, "invalid_metal"),
        ({"kind": "compound", "metal": "iron"}, "invalid_operation"),
        ({"kind": "store_start", "metal": "iron", "charges": 9},
         "invalid_operation_fields"),
    ]
    for operation, code in cases:
        with pytest.raises(MistbornRuntimeError) as caught:
            apply_feruchemy_operation(state, operation)
        assert caught.value.code == code
    assert FERUCHEMY_OPERATION_KINDS == frozenset({
        "store_start", "store_maintain", "store_stop",
        "tap_start", "tap_maintain", "tap_stop", "restore",
    })


def test_feruchemy_stop_requires_the_matching_active_mode():
    idle = feruchemy({"by_metal": {"iron": {"charge_current": 2}}})
    for kind in ("store_stop", "tap_stop"):
        with pytest.raises(MistbornRuntimeError) as caught:
            apply_feruchemy_operation(idle, {"kind": kind, "metal": "iron"})
        assert caught.value.code == "wrong_mode"


def test_linked_metadata_cannot_exceed_charges_actually_stored_at_capacity():
    state = feruchemy({"by_metal": {"copper": {"charge_current": 5}}}, metals=("copper",))
    state = apply_feruchemy_operation(
        state, {"kind": "store_start", "metal": "copper"})
    with pytest.raises(MistbornRuntimeError) as caught:
        apply_feruchemy_operation(state, {
            "kind": "store_maintain", "metal": "copper", "amount": 1,
            "linked_metadata": [{"id": "memory.new", "kind": "memory"}],
        })
    assert caught.value.code == "invalid_linked_metadata"


def test_linked_charge_metadata_follows_stored_and_tapped_charge():
    state = feruchemy({"by_metal": {"copper": {"charge_current": 0}}}, metals=("copper",))
    state = apply_feruchemy_operation(
        state, {"kind": "store_start", "metal": "copper"})
    state = apply_feruchemy_operation(state, {
        "kind": "store_maintain", "metal": "copper", "amount": 1,
        "linked_metadata": [{"id": "memory.childhood", "kind": "memory"}],
    })
    assert state["by_metal"]["copper"]["linked_charge_metadata"] == [
        {"id": "memory.childhood", "kind": "memory"}]
    state = apply_feruchemy_operation(state, {
        "kind": "tap_start", "metal": "copper", "amount": 1,
        "linked_id": "memory.childhood",
    })
    assert state["by_metal"]["copper"]["charge_current"] == 0
    assert state["by_metal"]["copper"]["linked_charge_metadata"] == []


def test_path_change_normalization_removes_inaccessible_metal_state_and_effects():
    effect = {
        "id": "effect.mistborn.iron",
        "source_id": "mistborn.allomancy.iron.action.burn",
        "target_id": "actor.hero",
        "parameters": {"art": "allomancy", "metal": "iron"},
        "selected_attribute": None,
        "stacking_identity": "mistborn.allomancy.iron",
        "duration_unit": "scene",
        "remaining_duration": 1,
        "trigger": "scene_end",
        "refresh_policy": "replace",
        "expiry_state": {},
        "expired": False,
    }
    source = {
        "allomancy": {
            "current_investiture": 3,
            "depleted_metals": ["iron", "steel"],
            "residual_by_power": {"iron": 1, "steel": 2},
            "active_burns": [effect],
        },
        "feruchemy": {
            "by_metal": {
                "iron": {"charge_current": 4, "mode": "tapping",
                         "active_effects": [effect]},
                "steel": {"charge_current": 3, "mode": "idle"},
            },
        },
    }
    result = normalize_mistborn_runtime(
        source, attributes=ATTRIBUTES, allomancy_rank=3, feruchemy_rank=3,
        era="era-1", accessible_allomantic_metals=("steel",),
        accessible_feruchemical_metals=("steel",),
        prior_accessible_allomantic_metals=("iron", "steel"),
        prior_accessible_feruchemical_metals=("iron", "steel"),
    )
    assert result["allomancy"]["depleted_metals"] == ["steel"]
    assert result["allomancy"]["residual_by_power"] == {"steel": 2}
    assert result["allomancy"]["active_burns"] == []
    assert set(result["feruchemy"]["by_metal"]) == {"steel"}


@pytest.mark.parametrize(
    ("era", "metals", "code"),
    [
        ("era-3", ("iron",), "invalid_era"),
        ("era-1", ("bendalloy",), "metal_wrong_era"),
        ("era-2", ("atium",), "metal_wrong_era"),
        ("era-1", ("wood",), "invalid_metal"),
    ],
)
def test_runtime_rejects_invalid_eras_and_metal_access(era, metals, code):
    with pytest.raises(MistbornRuntimeError) as caught:
        normalize_allomancy_state(
            {}, attributes=ATTRIBUTES, rank=1, era=era,
            accessible_metals=metals)
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("call", "code"),
    [
        (lambda: normalize_allomancy_state(
            {}, attributes=ATTRIBUTES, rank=1, era=[],
            accessible_metals=("iron",)), "invalid_era"),
        (lambda: apply_allomancy_operation(
            allomancy(), {"kind": []}), "invalid_operation"),
        (lambda: normalize_feruchemy_state(
            {"by_metal": {"iron": {"mode": []}}}, rank=1, era="era-1",
            accessible_metals=("iron",)), "invalid_mode"),
    ],
)
def test_unhashable_enum_containers_return_stable_domain_errors(call, code):
    with pytest.raises(MistbornRuntimeError) as caught:
        call()
    assert caught.value.code == code


def _mode_effect(instance_id, mode, *, refresh_policy="replace"):
    return {
        "id": instance_id,
        "source_id": "mistborn.feruchemy.iron.action.tap-weight",
        "target_id": "actor.hero",
        "parameters": {
            "art": "feruchemy", "metal": "iron", "feruchemy_mode": mode,
        },
        "selected_attribute": None,
        "stacking_identity": f"mistborn.feruchemy.iron.{mode}",
        "duration_unit": "scene",
        "remaining_duration": 1,
        "trigger": "scene_end",
        "refresh_policy": refresh_policy,
        "expiry_state": {},
        "expired": False,
    }


@pytest.mark.parametrize(
    ("kind", "mode"),
    [("store_start", "storing"), ("tap_start", "tapping")],
)
def test_repeated_mode_start_rejects_duplicate_effect_identity(kind, mode):
    state = feruchemy({"by_metal": {"iron": {"charge_current": 3}}})
    effect = _mode_effect("effect.mistborn.same", mode)
    operation = {"kind": kind, "metal": "iron", "effect": effect}
    if kind == "tap_start":
        operation["amount"] = 1
    started = apply_feruchemy_operation(state, operation)
    with pytest.raises(MistbornRuntimeError) as caught:
        apply_feruchemy_operation(started, operation)
    assert caught.value.code == "effect_conflict"


@pytest.mark.parametrize(
    ("kind", "mode"),
    [("store_start", "storing"), ("tap_start", "tapping")],
)
def test_repeated_mode_start_applies_shared_replace_stacking(kind, mode):
    state = feruchemy({"by_metal": {"iron": {"charge_current": 4}}})
    first = {"kind": kind, "metal": "iron",
             "effect": _mode_effect("effect.mistborn.first", mode)}
    second = {"kind": kind, "metal": "iron",
              "effect": _mode_effect("effect.mistborn.second", mode)}
    if kind == "tap_start":
        first["amount"] = second["amount"] = 1
    state = apply_feruchemy_operation(state, first)
    state = apply_feruchemy_operation(state, second)
    effects = state["by_metal"]["iron"]["active_effects"]
    assert [row["id"] for row in effects] == ["effect.mistborn.second"]
