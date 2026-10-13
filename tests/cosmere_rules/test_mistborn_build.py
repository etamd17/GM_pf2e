"""Mistborn build legality, scaling, and context-aware runtime normalization."""
import pytest

from systems.cosmere.mistborn_build import (MistbornBuild, allomancy_investiture_max,
                                             feruchemy_charge_max,
                                             metallic_art_scaling)


BASE_ATTRIBUTES = {"str": 2, "spd": 2, "int": 3, "wil": 2, "awa": 4, "pre": 1}


def build(*, trusted_goal_entitlements=(), **changes):
    data = {
        "name": "Metalborn", "level": 1, "origin_world": "scadrial", "era": "era-1",
        "ancestry": "Human", "starting_path": "agent",
        "heroic_paths": ["agent", "misting"],
        "metalborn_keys": ["mistborn.path.key.misting-snap"],
        "attributes": BASE_ATTRIBUTES,
        "skills": {"allomancy": 2, "ath": 1, "agi": 1, "hwp": 1, "prc": 1},
        "talents": [{"id": "test.one"}, {"id": "test.two"}], "expertises": [],
        "allomancy_rank": 2, "feruchemy_rank": 0,
        "allomantic_powers": {"iron": "nascent"}, "feruchemical_powers": {},
        "metal_access": {"allomancy": ["iron"], "feruchemy": []},
    }
    data.update(changes)
    return MistbornBuild(data,
                         trusted_goal_entitlements=trusted_goal_entitlements)


@pytest.mark.parametrize("path,eras", [
    ("misting", ("era-1", "era-2")),
    ("mistborn", ("era-1",)),
    ("feruchemist", ("era-1",)),
    ("ferring", ("era-2",)),
    ("twinborn", ("era-2",)),
])
def test_metalborn_path_era_matrix(path, eras):
    key = f"mistborn.path.key.{path}-key"
    for era in ("era-1", "era-2"):
        candidate = build(era=era, starting_path="agent", heroic_paths=["agent", path],
                          metalborn_keys=[key],
                          allomancy_rank=int(path in {"misting", "mistborn", "twinborn"}),
                          feruchemy_rank=int(path in {"feruchemist", "ferring", "twinborn"}))
        issues = candidate.hard_violations()
        assert (not any("era" in issue.lower() for issue in issues)) == (era in eras)


def test_one_ordinary_heroic_path_and_one_metalborn_path_are_legal():
    candidate = build(heroic_paths=["misting", "agent"])
    assert not any("ordinary heroic path" in issue.lower()
                   for issue in candidate.hard_violations())
    assert any("ordinary heroic path" in issue.lower() for issue in build(
        heroic_paths=["misting", "agent", "scholar"]).hard_violations())
    assert any("ordinary heroic path" in issue.lower() for issue in build(
        starting_path="misting", heroic_paths=["misting"]).hard_violations())
    assert any("starting path" in issue.lower() for issue in build(
        starting_path="", heroic_paths=["agent"]).hard_violations())
    assert any("unknown" in issue.lower() for issue in build(
        heroic_paths=["misting", "invented"]).hard_violations())


def test_transition_requires_an_exact_new_path_key_talent_for_metalborn_acquisition():
    ordinary = build(
        starting_path="agent", heroic_paths=["agent"], metalborn_keys=[],
        skills={"ath": 1, "agi": 1, "hwp": 1, "prc": 1},
        allomancy_rank=0, allomantic_powers={},
        metal_access={"allomancy": [], "feruchemy": []},
    )
    added = build(
        starting_path="agent", heroic_paths=["agent", "misting"],
        talents=list(ordinary.talents),
    )
    assert any("exact newly acquired path key talent" in issue.lower()
               for issue in added.validate_transition(ordinary))

    exact = build(
        starting_path="agent", heroic_paths=["agent", "misting"],
        talents=[*ordinary.talents, {"id": "mistborn.path.key.misting-snap"}],
    )
    assert not any("exact newly acquired path key talent" in issue.lower()
                   for issue in exact.validate_transition(ordinary))


def test_pure_lerasium_only_authorizes_the_mistborn_snap_key():
    ordinary = build(
        starting_path="agent", heroic_paths=["agent"], metalborn_keys=[],
        skills={"ath": 1, "agi": 1, "hwp": 1, "prc": 1},
        allomancy_rank=0, allomantic_powers={},
        metal_access={"allomancy": [], "feruchemy": []},
    )
    forged_feruchemist = build(
        starting_path="agent", heroic_paths=["agent", "feruchemist"],
        metalborn_keys=["mistborn.path.key.feruchemist-heritage"],
        skills={"feruchemy": 1, "ath": 1, "agi": 1, "hwp": 1, "prc": 1},
        allomancy_rank=0, feruchemy_rank=1,
        allomantic_powers={}, feruchemical_powers={"iron": "nascent"},
        metal_access={"allomancy": [], "feruchemy": ["iron"]},
        trusted_goal_entitlements=[
            "mistborn.entitlement.reward.pure-lerasium"],
    )

    issues = forged_feruchemist.validate_transition(
        ordinary,
        {"goal_entitlements": [
            "mistborn.entitlement.reward.pure-lerasium"]},
    )
    assert any("exact newly acquired path key talent" in issue.lower()
               for issue in issues)


def test_transition_never_adds_another_ordinary_heroic_path_by_name():
    ordinary = build(starting_path="agent", heroic_paths=["agent"],
                     metalborn_keys=[])
    added = build(starting_path="agent", heroic_paths=["agent", "scholar"],
                  metalborn_keys=[])
    assert any("ordinary heroic path" in issue.lower()
               for issue in added.validate_transition(ordinary))


def test_metalborn_keys_remain_exclusive_even_with_pure_lerasium():
    keys = ["mistborn.path.key.feruchemist-heritage", "mistborn.path.key.mistborn-snap"]
    ordinary = build(starting_path="agent",
                     heroic_paths=["agent", "feruchemist", "mistborn"],
                     metalborn_keys=keys, allomancy_rank=1, feruchemy_rank=1)
    assert any("metalborn" in issue.lower() for issue in ordinary.hard_violations())
    rewarded = build(starting_path="agent",
                     heroic_paths=["agent", "feruchemist", "mistborn"],
                     metalborn_keys=keys, allomancy_rank=1, feruchemy_rank=1,
                     goal_entitlements=["mistborn.entitlement.reward.pure-lerasium"],
                     trusted_goal_entitlements=[
                         "mistborn.entitlement.reward.pure-lerasium"])
    assert any("only one metalborn" in issue.lower()
               for issue in rewarded.hard_violations())
    forged = build(starting_path="agent",
                   heroic_paths=["agent", "feruchemist", "mistborn"],
                   metalborn_keys=keys, allomancy_rank=1, feruchemy_rank=1,
                   goal_entitlements=["pure-lerasium"])
    assert any("metalborn" in issue.lower() for issue in forged.hard_violations())


def test_metallic_art_ranks_are_derived_from_strict_skill_values():
    skills = {**build().skills, "allomancy": 2}
    for forged_rank in (True, 2.5, 999):
        candidate = build(skills=skills, allomancy_rank=forged_rank)
        assert candidate.allomancy_rank == 2
        assert any("allomancy rank" in issue.lower()
                   for issue in candidate.hard_violations())

    above_tier = build(skills={**skills, "allomancy": 3}, allomancy_rank=3)
    assert any("tier" in issue.lower() for issue in above_tier.hard_violations())


@pytest.mark.parametrize(
    ("changes", "issue_fragment"),
    [
        ({"level": True}, "level"),
        ({"level": 1.5}, "level"),
        ({"attributes": {**BASE_ATTRIBUTES, "awa": True}}, "attribute awa"),
        ({"attributes": {**BASE_ATTRIBUTES, "awa": 3.5}}, "attribute awa"),
    ],
)
def test_level_and_attribute_ranks_reject_boolean_and_fractional_values(
        changes, issue_fragment):
    candidate = build(**changes)
    assert any(issue_fragment in issue.lower()
               for issue in candidate.hard_violations())


def test_client_bonus_grants_cannot_expand_build_budgets():
    candidate = build(advancement_bonus_grants={
        "attribute_points": 50,
        "skill_ranks": 50,
        "talents": 50,
        "expertises": 50,
    })
    assert candidate.advancement_bonus_grants == {
        "attribute_points": 0,
        "skill_ranks": 0,
        "talents": 0,
        "expertises": 0,
    }
    assert any("bonus grants" in issue.lower()
               for issue in candidate.hard_violations())


def test_path_grants_control_power_counts_and_metal_access():
    too_many = build(
        allomantic_powers={"iron": "nascent", "steel": "nascent"},
        metal_access={"allomancy": ["iron", "steel"], "feruchemy": []},
    )
    assert any("exactly one allomantic power" in issue.lower()
               for issue in too_many.hard_violations())

    forged_access = build(
        metal_access={"allomancy": ["iron", "steel"], "feruchemy": []})
    assert forged_access.accessible_metals("allomancy") == ("iron",)
    assert any("metal access" in issue.lower()
               for issue in forged_access.hard_violations())


@pytest.mark.parametrize(
    ("field", "before_state", "after_state"),
    [
        ("allomantic_powers", "unlocked", "goal_active"),
        ("allomantic_powers", "unlocked", "nascent"),
        ("allomantic_powers", "goal_active", "nascent"),
        ("feruchemical_powers", "unlocked", "goal_active"),
        ("feruchemical_powers", "goal_active", "nascent"),
    ],
)
def test_advancement_cannot_downgrade_a_power_unlock_state(
        field, before_state, after_state):
    entitlement = "mistborn.entitlement.power.allomancy.iron.unlock"
    before = build(
        trusted_goal_entitlements=[entitlement],
        goal_entitlements=[entitlement],
        **{field: {"iron": before_state}},
    )
    after = build(
        trusted_goal_entitlements=[entitlement],
        goal_entitlements=[entitlement],
        **{field: {"iron": after_state}},
    )
    assert any("cannot downgrade" in issue.lower()
               for issue in after.validate_transition(before))


def test_advancement_allows_power_state_progression_with_exact_unlock_entitlement():
    goal_active = build(allomantic_powers={"iron": "goal_active"})
    assert not any("cannot downgrade" in issue.lower() for issue in
                   goal_active.validate_transition(build()))

    entitlement = "mistborn.entitlement.power.allomancy.iron.unlock"
    unlocked = build(
        trusted_goal_entitlements=[entitlement],
        goal_entitlements=[entitlement],
        allomantic_powers={"iron": "unlocked"},
    )
    issues = unlocked.validate_transition(
        goal_active, {"goal_entitlements": [entitlement]})
    assert not any("cannot downgrade" in issue.lower() for issue in issues)
    assert not any("requires its completed goal entitlement" in issue.lower()
                   for issue in issues)


@pytest.mark.parametrize("path,key", [
    ("misting", "mistborn.path.key.misting-snap"),
    ("mistborn", "mistborn.path.key.mistborn-snap"),
])
def test_era_one_atium_obeys_the_bound_path_grant(path, key):
    powers = ({metal: "nascent" for metal in (
        "iron", "steel", "tin", "pewter", "zinc", "brass", "copper",
        "bronze", "gold", "aluminum", "duralumin", "electrum", "atium")}
              if path == "mistborn" else {"atium": "nascent"})
    candidate = build(
        starting_path="agent", heroic_paths=["agent", path], metalborn_keys=[key],
        allomantic_powers=powers,
        metal_access={"allomancy": sorted(powers), "feruchemy": []},
    )
    assert any("atium starts unlocked" in issue.lower()
               for issue in candidate.hard_violations())

    powers["atium"] = "unlocked"
    granted = build(
        starting_path="agent", heroic_paths=["agent", path], metalborn_keys=[key],
        allomantic_powers=powers,
        metal_access={"allomancy": sorted(powers), "feruchemy": []},
    )
    assert not any("atium" in issue.lower()
                   for issue in granted.hard_violations())


def test_atium_unlock_exception_is_read_from_the_exact_bound_path_record():
    key = "mistborn.path.key.misting-snap"
    payload = build(
        allomantic_powers={"atium": "unlocked"},
        metal_access={"allomancy": ["atium"], "feruchemy": []},
    ).to_dict()
    base_rule = {
        "path": "misting", "eras": ("era-1", "era-2"),
        "prerequisites": ("ancestry:human|koloss_blooded", "no_snap_or_heritage"),
    }
    not_granted = MistbornBuild(payload, path_rules={key: base_rule})
    assert any("atium" in issue.lower() and "goal entitlement" in issue.lower()
               for issue in not_granted.hard_violations())

    granted = MistbornBuild(
        payload,
        path_rules={key: {**base_rule, "effect_ops": ["atium_starts_unlocked"]}},
    )
    assert not any("atium" in issue.lower()
                   for issue in granted.hard_violations())


def test_exact_path_key_identity_and_all_heroic_path_era_scopes_are_enforced():
    counterfeit = build(metalborn_keys=["mistborn.path.key.misting-counterfeit"])
    assert any("unknown metalborn path key" in issue.lower()
               for issue in counterfeit.hard_violations())

    wrong_era = build(heroic_paths=["agent", "misting", "twinborn"])
    assert any("twinborn" in issue.lower() and "era" in issue.lower()
               for issue in wrong_era.hard_violations())


def test_pure_lerasium_entitlement_does_not_add_a_second_metalborn_path():
    keys = ["mistborn.path.key.feruchemist-heritage",
            "mistborn.path.key.mistborn-snap"]
    payload = {**build().to_dict(), "starting_path": "agent",
               "heroic_paths": ["agent", "feruchemist", "mistborn"],
               "metalborn_keys": keys,
               "goal_entitlements": [
                   "mistborn.entitlement.reward.pure-lerasium"]}
    forged = MistbornBuild(payload)
    assert any("metalborn" in issue.lower() for issue in forged.hard_violations())

    authoritative = MistbornBuild(
        payload,
        trusted_goal_entitlements=[
            "mistborn.entitlement.reward.pure-lerasium"],
    )
    assert any("only one metalborn" in issue.lower()
               for issue in authoritative.hard_violations())


def test_metalborn_ancestry_prerequisites_are_server_validated():
    assert any("ancestry" in issue.lower() for issue in build(
        ancestry="Kandra").hard_violations())
    assert any("ancestry" in issue.lower() for issue in build(
        ancestry="Koloss-blooded", starting_path="agent",
        heroic_paths=["agent", "mistborn"],
        metalborn_keys=["mistborn.path.key.mistborn-snap"]).hard_violations())
    assert not any("ancestry" in issue.lower() for issue in build(
        ancestry="Koloss-blooded").hard_violations())


def test_governing_attributes_and_resource_maxima_are_exact():
    candidate = build()
    assert candidate.governing_attribute("allomancy") == "wil"
    assert candidate.governing_attribute("feruchemy") == "int"
    assert allomancy_investiture_max(BASE_ATTRIBUTES) == 6
    assert candidate.investiture_max() == 6
    assert feruchemy_charge_max(0) == 2
    assert feruchemy_charge_max(4) == 6


@pytest.mark.parametrize("rank,expected", [
    (0, {"limit": 1, "die": None, "range_ft": 10}),
    (1, {"limit": 1, "die": "d4", "range_ft": 20}),
    (2, {"limit": 2, "die": "d6", "range_ft": 40}),
    (3, {"limit": 3, "die": "d8", "range_ft": 80}),
    (4, {"limit": 4, "die": "d10", "range_ft": 160}),
    (5, {"limit": 5, "die": "d12", "range_ft": 320}),
    (6, {"limit": 6, "die": "d20", "range_ft": 640}),
    (9, {"limit": 9, "die": "d20", "range_ft": 640}),
])
def test_metallic_art_rank_scaling_literals(rank, expected):
    assert metallic_art_scaling(rank) == expected


def test_runtime_normalization_uses_current_and_prior_build_context():
    from systems.cosmere.mistborn_profile import normalize_mistborn_runtime_state

    feruchemy_entitlement = "mistborn.entitlement.power.feruchemy.iron.unlock"
    current = build(era="era-2", feruchemy_rank=2,
                    skills={"allomancy": 2, "feruchemy": 2, "ath": 1, "agi": 1,
                            "hwp": 1, "prc": 1},
                    heroic_paths=["agent", "twinborn"],
                    metalborn_keys=["mistborn.path.key.twinborn-heritage"],
                    goal_entitlements=[feruchemy_entitlement],
                    trusted_goal_entitlements=[feruchemy_entitlement],
                    feruchemical_powers={"iron": "unlocked"},
                    metal_access={"allomancy": ["iron", "steel"], "feruchemy": ["iron"]})
    state = {
        "mistborn": {
            "allomancy": {"current_investiture": 99, "depleted_metals": ["iron", "tin"],
                           "vial_inventory": {"standard": {
                               "count": 2, "metals": ["iron", "steel"]}}},
            "feruchemy": {"by_metal": {"iron": {"charge_current": 99, "mode": "storing"},
                                          "steel": {"charge_current": 3, "mode": "tapping"}}},
        },
    }
    normalized = normalize_mistborn_runtime_state(state, "cosmere.actor.one", current,
                                                   prior_build=build(era="era-2"))
    allo = normalized["mistborn"]["allomancy"]
    assert allo["max_investiture"] == 6 and allo["current_investiture"] == 6
    assert allo["depleted_metals"] == ["iron"]
    assert allo["vial_inventory"] == {
        "standard": {"count": 2, "metals": ["iron", "steel"]}}
    assert allo["governing_attribute"] == "willpower"
    feru = normalized["mistborn"]["feruchemy"]["by_metal"]
    assert set(feru) == {"iron"}
    assert feru["iron"] == {"charge_current": 4, "charge_max": 4, "mode": "storing",
                            "active_effects": [], "linked_charge_metadata": []}
    # Normalization starts a fresh pool full; the authoritative scene-start
    # operation applies the Surprised exception.
    from systems.cosmere.mistborn_runtime import apply_allomancy_operation
    fresh = normalize_mistborn_runtime_state({}, "cosmere.actor.two", current)
    surprised = apply_allomancy_operation(
        fresh["mistborn"]["allomancy"], {"kind": "scene_start", "surprised": True})
    assert surprised["current_investiture"] == 1


def test_runtime_drops_inaccessible_art_state_when_path_changes():
    from systems.cosmere.mistborn_profile import normalize_mistborn_runtime_state

    prior = build(era="era-2", feruchemy_rank=2,
                  skills={"allomancy": 2, "feruchemy": 2, "ath": 1, "agi": 1,
                          "hwp": 1, "prc": 1},
                  heroic_paths=["agent", "twinborn"],
                  metalborn_keys=["mistborn.path.key.twinborn-heritage"],
                  feruchemical_powers={"iron": "unlocked"},
                  metal_access={"allomancy": ["iron"], "feruchemy": ["iron"]})
    state = {"mistborn": {"allomancy": {"current_investiture": 3,
                                           "depleted_metals": ["iron"]},
                           "feruchemy": {"by_metal": {"iron": {
                               "charge_current": 3, "charge_max": 4, "mode": "tapping"}}}}}
    current = MistbornBuild({**prior.to_dict(), "allomancy_rank": 0,
                             "feruchemy_rank": 0, "metalborn_keys": [],
                             "heroic_paths": ["agent"],
                             "skills": {"ath": 1, "agi": 1, "hwp": 1, "prc": 1},
                             "allomantic_powers": {}, "feruchemical_powers": {},
                             "metal_access": {"allomancy": [], "feruchemy": []}})
    normalized = normalize_mistborn_runtime_state(state, "cosmere.actor.one", current,
                                                   prior_build=prior)
    assert normalized["mistborn"]["allomancy"] == {
        "governing_attribute": "willpower", "rank": 0, "era": "era-2",
        "accessible_metals": [], "current_investiture": 0, "max_investiture": 0,
        "depleted_metals": [], "residual_by_power": {}, "vial_inventory": {},
        "rare_metal_access": {}, "active_burns": []}
    assert normalized["mistborn"]["feruchemy"]["by_metal"] == {}
