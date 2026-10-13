from copy import deepcopy

import pytest

from systems.cosmere.mistborn_encounters import (
    DIFFICULTY_MULTIPLIERS,
    THREAT_BY_LEVEL_DELTA,
    MistbornEncounterError,
    difficulty_targets,
    encounter_budget_for_party,
    mistborn_encounter_budget,
    threat_value,
)
from systems.cosmere.rules.profiles import package_pin, resolve_profile
from systems.cosmere.rules.schema import CosmereRulesBinding, to_dict
from systems.cosmere.rules.seeds import load_seed_registry
from systems.cosmere.rules.paths import registry_path


EXPECTED_THREAT = {
    -3: {"minion": 4, "rival": 8, "boss": 32},
    -2: {"minion": 2, "rival": 4, "boss": 16},
    -1: {"minion": 1, "rival": 2, "boss": 8},
    0: {"minion": 0.5, "rival": 1, "boss": 4},
    1: {"minion": 0.25, "rival": 0.5, "boss": 2},
    2: {"minion": 0, "rival": 0.25, "boss": 1},
    3: {"minion": 0, "rival": 0, "boss": 0.5},
}


@pytest.mark.parametrize(
    ("delta", "role", "expected"),
    [(delta, role, value)
     for delta, row in EXPECTED_THREAT.items()
     for role, value in row.items()],
)
def test_every_mistborn_threat_cell_is_a_literal(delta, role, expected):
    assert threat_value(delta, role) == expected


def test_threat_table_preserves_zero_cells_and_is_detached_from_callers():
    assert THREAT_BY_LEVEL_DELTA == EXPECTED_THREAT
    copied = deepcopy(THREAT_BY_LEVEL_DELTA)
    copied[3]["minion"] = 999
    assert threat_value(3, "minion") == 0
    assert threat_value(3, "rival") == 0


def test_difficulty_targets_use_exact_per_character_multipliers():
    assert DIFFICULTY_MULTIPLIERS == {"easy": 0.5, "average": 1, "hard": 1.5}
    assert difficulty_targets(1) == {"easy": 0.5, "average": 1, "hard": 1.5}
    assert difficulty_targets(4) == {"easy": 2, "average": 4, "hard": 6}


def test_budget_sums_explicit_delta_rows_without_inferring_exponential_values():
    adversaries = [
        {"id": "adversary.coinshot", "role": "rival", "level_delta": 0,
         "count": 2},
        {"id": "adversary.hazecrusher", "role": "boss", "level_delta": 2},
        {"id": "adversary.weak-minions", "role": "minion", "level_delta": 3,
         "count": 20},
    ]
    before = deepcopy(adversaries)
    result = mistborn_encounter_budget(adversaries, party_size=4)
    assert adversaries == before
    assert result["threat"] == 3
    assert result["targets"] == {"easy": 2, "average": 4, "hard": 6}
    assert result["entries"][2]["unit_threat"] == 0
    assert result["automation_disposition"] == "calculated"
    assert result["source"] == {
        "artifact_id": "mistborn.handbook",
        "printed_pages": [359, 360, 361, 362, 363, 364, 365, 366],
        "pdf_pages": [362, 363, 364, 365, 366, 367, 368, 369],
    }


def test_profile_budget_derives_table_delta_from_authoritative_party_and_adversary_tiers():
    result = encounter_budget_for_party(
        [{"id": "pc.one", "tier": 1}, {"id": "pc.two", "tier": 1}],
        [{"id": "mistborn.adversary.advisor", "tier": 2,
          "role": "rival", "count": 1}],
    )

    assert result["entries"] == [{
        "id": "mistborn.adversary.advisor", "role": "rival",
        "level_delta": -1, "count": 1, "unit_threat": 2,
        "threat": 2,
    }]
    assert result["party_tier"] == 1
    assert result["threat"] == 2


def test_profile_budget_rejects_ambiguous_mixed_party_tiers():
    with pytest.raises(MistbornEncounterError) as caught:
        encounter_budget_for_party(
            [{"id": "pc.one", "tier": 1}, {"id": "pc.two", "tier": 2}],
            [{"id": "foe", "tier": 1, "role": "rival"}],
        )
    assert caught.value.code == "mixed_party_tier"


@pytest.mark.skipif(not registry_path().is_file(),
                    reason="private Mistborn rules data is not installed")
def test_exact_audited_profile_budgets_a_real_catalog_adversary_without_client_delta():
    import app as host

    registry = load_seed_registry()
    audited = lambda pack_id: next(
        pack for pack in registry.pack_versions.values()
        if pack.id == pack_id and pack.version == "1.1.0"
        and (pack_id != "cosmere.mistborn.era-1" or "era-1" in pack.eras))
    binding = CosmereRulesBinding(
        package_pin(registry.packs["cosmere.core"]),
        (package_pin(audited("cosmere.mistborn.core")),
         package_pin(audited("cosmere.mistborn.era-1"))),
        "scadrial", ("era-1",), ("scadrial",))
    profile = resolve_profile({
        "rules_binding": to_dict(binding),
        "origin_world": "scadrial", "era": "era-1",
    }, registry=registry, spoiler_scope="gm")
    record = next(row for row in profile.catalog()["catalogs"]["adversary"]
                  if "era-1" in row["eras"])
    projected = host._cosmere_catalog_adversary(
        profile, record["id"], world="scadrial", era="era-1")

    result = profile.encounter_budget(
        [{"id": "pc.one", "tier": projected["tier"]}], [projected])

    assert result["entries"][0]["id"] == record["id"]
    assert result["entries"][0]["level_delta"] == 0


@pytest.mark.parametrize(
    ("call", "code"),
    [
        (lambda: threat_value(4, "rival"), "invalid_level_delta"),
        (lambda: threat_value(0, "solo"), "invalid_role"),
        (lambda: threat_value(0, {}), "invalid_role"),
        (lambda: threat_value(True, "rival"), "invalid_level_delta"),
        (lambda: difficulty_targets(0), "invalid_party_size"),
        (lambda: mistborn_encounter_budget([], party_size=True), "invalid_party_size"),
        (lambda: mistborn_encounter_budget(
            [{"id": "x", "role": "rival", "level_delta": 0, "count": 0}],
            party_size=2), "invalid_count"),
    ],
)
def test_encounter_contract_rejects_invalid_domain_values(call, code):
    with pytest.raises(MistbornEncounterError) as caught:
        call()
    assert caught.value.code == code
