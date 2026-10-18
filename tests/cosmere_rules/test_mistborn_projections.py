"""Backend-neutral Mistborn party and tracker projections."""
from copy import deepcopy
from types import SimpleNamespace


def _actor_document(*, world="scadrial", era="era-2", investiture=4):
    return {
        "name": "Metalborn",
        "type": "character",
        "system": {
            "level": {"value": 1},
            "tier": 1,
            "role": "hero",
            "origin_world": world,
            "era": era,
            "radiant_order": "" if world == "scadrial" else "windrunner",
            "attributes": {
                key: {"value": 2, "bonus": 0}
                for key in ("str", "spd", "int", "wil", "awa", "pre")
            },
            "defenses": {},
            "resources": {
                "hea": {"value": 12, "max": {"override": 12, "useOverride": True}},
                "foc": {"value": 4, "max": {"override": 4, "useOverride": True}},
                "inv": {
                    "value": investiture,
                    "max": {"override": 4, "useOverride": True},
                },
            },
            "mistborn": {
                "allomancy": {
                    "current_investiture": investiture,
                    "max_investiture": 4,
                    "depleted_metals": ["iron"],
                    "vial_inventory": {
                        "vial.standard": {"count": 2, "metals": ["iron"]}
                    },
                    "active_burns": [],
                },
                "feruchemy": {
                    "by_metal": {
                        "steel": {
                            "charge_current": 2,
                            "charge_max": 3,
                            "mode": "storing",
                            "active_effects": [],
                            "linked_charge_metadata": [],
                        }
                    }
                },
            },
            "skills": {},
            "deflect": {},
        },
        "items": [],
    }


def test_scadrian_investiture_does_not_misclassify_actor_as_radiant():
    from systems.cosmere.actor import CosmereActor

    actor = CosmereActor(_actor_document())
    block = actor.tracker_block()

    assert actor.is_radiant is False
    assert actor.origin_world == "scadrial"
    assert actor.era == "era-2"
    assert block["origin_world"] == "scadrial"
    assert block["era"] == "era-2"
    assert block["investiture_current"] == 4
    assert block["mistborn"]["allomancy"]["depleted_metals"] == ["iron"]
    assert block["mistborn"]["feruchemy"]["by_metal"]["steel"]["mode"] == "storing"


def test_rosharan_radiant_identity_remains_explicit():
    from systems.cosmere.actor import CosmereActor

    actor = CosmereActor(_actor_document(world="roshar", era=None))

    assert actor.is_radiant is True
    assert actor.tracker_block()["radiant_order"] == "windrunner"


def test_party_projection_uses_nested_mistborn_resources(monkeypatch):
    import systems.cosmere.party as party

    canonical = {
        "id": "cosmere.actor.metalborn",
        "name": "Metalborn",
        "origin_world": "scadrial",
        "era": "era-2",
        "build": {
            "name": "Metalborn",
            "skills": {"allomancy": 1, "feruchemy": 1},
            "talents": [],
        },
        "derived": {
            "level": 1,
            "tier": 1,
            "defenses": {"phy": 14, "cog": 14, "spi": 14},
            "health_max": 12,
            "focus_max": 4,
            "investiture_max": 4,
        },
        "runtime_state": {
            "health": 11,
            "focus": 3,
            # This legacy projection is deliberately stale. The nested state is
            # canonical for a Mistborn profile and must win.
            "investiture": 1,
            "effects": [],
            "mistborn": deepcopy(_actor_document()["system"]["mistborn"]),
        },
    }
    monkeypatch.setattr(
        party,
        "normalize_character_document",
        lambda *args, **kwargs: deepcopy(canonical),
    )

    member = party.project_member({"id": canonical["id"]})

    assert member["resources"]["investiture"] == {"current": 4, "max": 4}
    assert member["mistborn"]["allomancy"]["depleted_metals"] == ["iron"]
    assert member["mistborn"]["feruchemy"]["by_metal"]["steel"] == {
        "charge_current": 2,
        "charge_max": 3,
        "mode": "storing",
        "active_effects": [],
        "linked_charge_metadata": [],
    }


def test_live_tracker_sync_reads_nested_allomancy_and_feruchemy_state(monkeypatch):
    import app as host

    actor = SimpleNamespace(
        system="cosmere",
        is_pc=True,
        restore_id="cosmere.actor.metalborn",
        character_revision=2,
        current_investiture=0,
        mistborn_runtime={},
    )
    monkeypatch.setattr(host, "ACTIVE_ENCOUNTER", [actor])
    state = {
        "mistborn": deepcopy(_actor_document()["system"]["mistborn"]),
    }

    assert host._sync_cosmere_combatant_state(
        actor.restore_id, state, character_revision=3) is True
    assert actor.current_investiture == 4
    assert actor.mistborn_runtime["allomancy"]["depleted_metals"] == ["iron"]
    assert actor.mistborn_runtime["feruchemy"]["by_metal"]["steel"]["mode"] == "storing"
    assert actor.character_revision == 3


def test_service_scope_accepts_explicit_enabled_world_and_scopes_eras(monkeypatch):
    import pytest
    import app as host

    profile = SimpleNamespace(binding=SimpleNamespace(
        default_world="roshar",
        allowed_worlds=("roshar", "scadrial"),
        allowed_eras=("era-1", "era-2"),
    ))
    monkeypatch.setattr(host, "_load_campaign_config", lambda: {})

    assert host._cosmere_service_scope(
        profile, {"world": "scadrial", "era": "era-2"}) == (
            "scadrial", "era-2")
    assert host._cosmere_service_scope(profile, {"world": "roshar"}) == (
        "roshar", None)
    with pytest.raises(Exception, match="era|Scadrial"):
        host._cosmere_service_scope(
            profile, {"world": "roshar", "era": "era-1"})
    with pytest.raises(Exception, match="world|binding"):
        host._cosmere_service_scope(profile, {"world": "nalthis"})
