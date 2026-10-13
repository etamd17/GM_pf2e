"""Observed source identity and normalized seed packages are pinned mechanically."""
import importlib
import json
from pathlib import Path
import pytest
from systems.cosmere.rules.paths import registry_path

ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = registry_path().parent
pytestmark = pytest.mark.skipif(not registry_path().is_file(),
                                reason="private Cosmere rules data is not installed")

# Independent observed values supplied in the task brief, never derived from the registry.
FACTS = {
    "mistborn.handbook": ("MB001_Mistborn_Handbook_digital.pdf", "v1.02", 247547589, 410, "1594e01b2bd09623128114072e4b8d279787760eb16550782fbb5b54990cce13"),
    "mistborn.world-guide": ("MB002_Mistborn_World_Guide_digital.pdf", "v1.02", 172818796, 290, "a6f687781d6da04ebe2f56842cfc6d122a711237c3ce900e8c66edcff0649b22"),
    "mistborn.legacy.maps-tokens": ("MB003_Mistborn_Legacy_battlemaps_and_tokens.pdf", "v1.02", 94376824, 82, "b1634bdee96b9936f0ccaba40a87e5e75dc096540c8e714a50d6ef8cf2288435"),
    "mistborn.legacy": ("MB003_Mistborn_Legacy_digital.pdf", "v1.02", 117857494, 270, "a6f6a5a2b44de0e5778d412745aa50b489a96f8bd15edd48a8f98a305ba5fd63"),
    "mistborn.welcome-to-scadrial": ("MB004_Welcome_to_Scadrial_digital.pdf", "v1.02", 14893686, 28, "be79276507fcd6a101e63983faea152675a8b70bc3e79c99418de020ef0953a9"),
    "mistborn.legacy.gm-tools": ("MB017_Mistborn_Legacy_GM_Tools.pdf", "v1.02", 7371551, 111, "85ec63a3f75e19943a2a1195c91491ebac7d7a64c48f89732c91691f3ba90159"),
    "stormlight.handbook": ("Cosmere RPG - Stormlight Handbook - Rulebook (Brandon Sanderson) (z-library.sk, 1lib.sk, z-lib.sk).pdf", "v1.01", 205426386, 392, "8068ac3fee2c9da0f8d2e42d5e97cc95cb1d74cbdc10e25d2b2c30669f3a52ed"),
    "stormlight.world-guide": ("Cosmere RPG - Stormlight World Guide - Lorebook (Brandon Sanderson) (z-library.sk, 1lib.sk, z-lib.sk).pdf", "v1.01", 213575280, 280, "5a31183807b7369f9128f9b73bca70c344e795740e64652643d4ee64eac49431"),
    "stormlight.stonewalkers.gm-tools": ("Stonewalkers GM Tools.pdf", "unverified", 620286, 41, "8537d65ebefe761ce562798969743acb64c291d3bfccc0e6063b0655f5d0f3ec"),
    "stormlight.stonewalkers.handouts": ("Stonewalkers Handouts.pdf", "unverified", 55805300, 40, "842266e8c5d1a82d87fe51af2f1fe31185eb66b192d54637c114b8aea8e999af"),
    "stormlight.stonewalkers.maps": ("Stonewalkers Maps.pdf", "unverified", 178500263, 55, "53e4c8d9d1adf20af4b8b43474b6d833fcb27fd2d071b0c25e22eaa8bea464e9"),
    "stormlight.stonewalkers": ("Stonewalkers.pdf", "v1.02", 68757827, 173, "c89bb9a43c2e408562abad17223d38448b81b0b39593b1d30f8fc0614b1ef021"),
    "stormlight.first-step": ("The First Step.pdf", "v1.02", 13958137, 44, "7f5741dc116ba4f0be3ff3a6bcafa335622806b617929aa429a8e009ce850b5d"),
}


def test_all_source_facts_and_quarantine_state_are_preserved():
    registry = importlib.import_module("systems.cosmere.rules.seeds").load_seed_registry()
    assert set(registry.artifacts) == set(FACTS)
    for product_id, facts in FACTS.items():
        artifact = registry.artifacts[product_id]
        assert (artifact.basename, artifact.edition, artifact.byte_count, artifact.page_count, artifact.sha256) == facts
        assert artifact.rights_state == ("quarantined" if product_id in {"stormlight.handbook", "stormlight.world-guide"} else "private")


def test_published_adventure_support_inherits_adventure_authority():
    registry = importlib.import_module("systems.cosmere.rules.seeds").load_seed_registry()
    for product_id in ("mistborn.legacy.gm-tools", "mistborn.legacy.maps-tokens",
                       "stormlight.stonewalkers.gm-tools", "stormlight.stonewalkers.handouts",
                       "stormlight.stonewalkers.maps"):
        assert registry.artifacts[product_id].authority_tier == "adventure"


def test_seed_packages_have_dependency_provenance_and_provisional_audits():
    registry = importlib.import_module("systems.cosmere.rules.seeds").load_seed_registry()
    assert set(registry.packs) == {"cosmere.core", "cosmere.stormlight.legacy", "cosmere.mistborn.era-1",
        "cosmere.mistborn.era-2", "cosmere.mistborn.core", "cosmere.stonewalkers",
        "cosmere.first-step", "cosmere.welcome-to-scadrial", "cosmere.mistborn.legacy"}
    for pack in registry.packs.values():
        assert pack.sources
        assert pack.audit_status == "provisional"
        assert pack.version == (
            "1.1.0" if pack.id == "cosmere.mistborn.core" else "1.0.0"
        )
        assert len(pack.package_hash) == 64
        if pack.id != "cosmere.core":
            assert "cosmere.core" in pack.dependencies


def test_seed_registry_publishes_audited_mistborn_versions_without_upgrading_legacy_pins():
    registry = importlib.import_module("systems.cosmere.rules.seeds").load_seed_registry()
    expected = {
        "cosmere.mistborn.core": 695,
        "cosmere.mistborn.era-1": 71,
        "cosmere.mistborn.era-2": 152,
    }

    for pack_id, record_count in expected.items():
        audited = [
            pack
            for (candidate_id, version, _package_hash), pack
            in registry.pack_versions.items()
            if candidate_id == pack_id and version == "1.1.0"
        ]
        assert len(audited) == 1
        assert len(audited[0].records) == record_count

    assert registry.packs["cosmere.mistborn.era-1"].version == "1.0.0"
    assert registry.packs["cosmere.mistborn.era-1"].records == ()
    assert registry.packs["cosmere.mistborn.era-2"].version == "1.0.0"
    assert registry.packs["cosmere.mistborn.era-2"].records == ()


def test_audited_mistborn_era_pins_require_and_resolve_the_shared_audited_core():
    registry = importlib.import_module("systems.cosmere.rules.seeds").load_seed_registry()
    schema = importlib.import_module("systems.cosmere.rules.schema")

    def audited(pack_id):
        return next(
            pack
            for (candidate_id, version, _package_hash), pack
            in registry.pack_versions.items()
            if candidate_id == pack_id and version == "1.1.0"
        )

    cosmere_core = registry.packs["cosmere.core"]
    mistborn_core = audited("cosmere.mistborn.core")
    era_1 = audited("cosmere.mistborn.era-1")
    era_2 = audited("cosmere.mistborn.era-2")
    assert era_1.dependencies == ("cosmere.core", "cosmere.mistborn.core")
    assert era_2.dependencies == ("cosmere.core", "cosmere.mistborn.core")

    pin = lambda pack: schema.PackagePin(pack.id, pack.version, pack.package_hash)
    binding = schema.CosmereRulesBinding(
        pin(cosmere_core), (pin(mistborn_core), pin(era_1), pin(era_2)),
        "scadrial", ("era-1", "era-2"),
    )
    assert registry.resolve_binding(binding) == (
        cosmere_core, mistborn_core, era_1, era_2,
    )


def test_only_audited_random_table_enters_the_generic_roll_engine():
    registry = importlib.import_module("systems.cosmere.rules.seeds").load_seed_registry()
    core = next(
        pack
        for (pack_id, version, _package_hash), pack
        in registry.pack_versions.items()
        if pack_id == "cosmere.mistborn.core" and version == "1.1.0"
    )
    rollable = {record.id: record for record in core.records if record.kind == "table"}
    assert set(rollable) == {"mistborn.table.crafting-upgrades-drawbacks"}
    assert rollable["mistborn.table.crafting-upgrades-drawbacks"].parameters[
        "results"
    ] == (
        {"id": "roll-1", "weight": 1, "value": {
            "drawback": "Material cost doubles.", "roll": 1,
            "upgrade": "Salvage usable material so material cost is waived.",
        }},
        {"id": "roll-2", "weight": 1, "value": {
            "drawback": "Tools or reputation are damaged.", "roll": 2,
            "upgrade": "Public acclaim may attract a patron.",
        }},
        {"id": "roll-3", "weight": 1, "value": {
            "drawback": "Finished item weighs twice as much.", "roll": 3,
            "upgrade": "Finished item weighs half as much.",
        }},
        {"id": "roll-4", "weight": 1, "value": {
            "drawback": "Item breaks whenever its user rolls a complication.",
            "roll": 4,
            "upgrade": "Item ignores a break-on-complication result.",
        }},
        {"id": "roll-5", "weight": 1, "value": {
            "drawback": "Item is an obvious eyesore.", "roll": 5,
            "upgrade": "Item is notably beautiful.",
        }},
        {"id": "roll-6", "weight": 1, "value": {
            "drawback": "The work attracts hostility from established makers or nobles.",
            "roll": 6,
            "upgrade": "Recognition may attract a patron.",
        }},
    )


def test_seed_authoring_recompiles_to_identical_bytes_without_local_paths():
    data_path = DATA_PATH
    data = json.loads((data_path / "authoring.json").read_text(encoding="utf-8"))
    raw = importlib.import_module("systems.cosmere.rules.compiler").compile_registry(data)
    assert raw == (data_path / "registry.json").read_bytes()
    assert b"C:\\" not in raw and b"/Users/" not in raw and b"local_path" not in raw


def test_profile_boundary_declares_the_required_server_operations():
    module = importlib.import_module("systems.cosmere.rules.profiles")
    required = {"catalog", "level_grants", "validate_build", "project_actor", "normalize_runtime_state",
                "apply_action", "encounter_budget", "tracker_block"}
    assert all(callable(getattr(module.CosmereRulesProfile, name, None)) for name in required)
