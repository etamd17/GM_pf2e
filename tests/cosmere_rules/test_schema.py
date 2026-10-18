"""Trust-boundary and immutable record contracts for the Cosmere kernel."""
from dataclasses import FrozenInstanceError, replace
import importlib

import pytest


def api():
    return importlib.import_module("systems.cosmere.rules.schema")


def artifact(**changes):
    value = dict(product_id="mistborn.handbook", basename="book.pdf", edition="v1.02",
                 sha256="a" * 64, byte_count=123, page_count=410,
                 authority_tier="handbook", extraction_class="rules",
                 spoiler_scope="player", rights_state="private")
    value.update(changes)
    return api().SourceArtifact(**value)


def rule(**changes):
    value = dict(id="cosmere.core.test", version="1.0.0", kind="rule", name="Test",
                 summary="Original mechanical summary", citations=(api().SourceRef("mistborn.handbook", 1, 2),),
                 worlds=("scadrial",), eras=("era-1",), spoiler_scope="player",
                 automation="reference", audit_status="provisional", parameters={"cost": [1, 2]})
    value.update(changes)
    return api().RuleRecord(**value)


@pytest.mark.parametrize("changes", [
    {"product_id": "bare"}, {"product_id": "Mistborn.Book"},
    {"basename": "C:\\private\\book.pdf"}, {"basename": "../book.pdf"},
    {"sha256": "A" * 64}, {"sha256": "a" * 63},
    {"byte_count": True}, {"page_count": 0}, {"edition": ""},
    {"authority_tier": "random"}, {"extraction_class": "random"},
    {"spoiler_scope": "random"}, {"rights_state": "public"},
])
def test_artifacts_reject_invalid_identity_and_metadata(changes):
    with pytest.raises(ValueError):
        artifact(**changes)


@pytest.mark.parametrize("pages", [(0, 1), (2, 1), (True, 2)])
def test_source_refs_reject_invalid_page_ranges(pages):
    with pytest.raises(ValueError):
        api().SourceRef("mistborn.handbook", *pages)


@pytest.mark.parametrize("changes", [
    {"id": "test"}, {"version": "1.0"}, {"version": "01.0.0"},
    {"version": "1.0.0-01"}, {"citations": ()}, {"automation": "automatic"},
    {"worlds": ("mars",)}, {"eras": ("era-3",)},
    {"worlds": ("roshar",), "eras": ("era-1",)},
    {"spoiler_scope": "all"}, {"audit_status": "approved-ish"},
    {"parameters": {"file": "C:\\private\\book.pdf"}},
    {"parameters": {"raw_text": "verbatim"}},
    {"parameters": {"cost": float("nan")}},
])
def test_rules_reject_invalid_scopes_versions_and_missing_citations(changes):
    with pytest.raises(ValueError):
        rule(**changes)


def test_records_are_deeply_immutable_and_detach_authoring_objects():
    source = artifact()
    with pytest.raises(FrozenInstanceError):
        source.page_count = 9
    choices = {"cost": [1, 2]}
    record = rule(parameters=choices)
    choices["cost"].append(3)
    assert record.parameters["cost"] == (1, 2)
    with pytest.raises(TypeError):
        record.parameters["cost"] = ()


def test_rules_preserve_normalized_prerequisites_and_reference_graph_edges():
    record = rule(prerequisites=("rank:allomancy>=1", "power_state=unlocked"),
                  references=("mistborn.power.allomancy.iron",
                              "mistborn.resource.metalmind.*"))
    assert record.prerequisites == ("rank:allomancy>=1", "power_state=unlocked")
    assert record.references[-1] == "mistborn.resource.metalmind.*"
    for changes in ({"prerequisites": ("",)}, {"references": ("bare",)},
                    {"references": ("mistborn.power.IRON",)}):
        with pytest.raises(ValueError):
            rule(**changes)


def test_pack_identity_dependency_and_provenance_are_required():
    s = api()
    valid = s.ContentPack(id="cosmere.core", version="1.0.0", kind="core", dependencies=(),
                          sources=("mistborn.handbook",), worlds=("cosmere",), eras=(),
                          spoiler_scope="player", audit_status="provisional", records=(rule(),))
    for changes in ({"dependencies": ("cosmere.core",)}, {"sources": ()},
                    {"dependencies": ("cosmere.other", "cosmere.other")},
                    {"package_hash": "bad"}, {"version": "1"}, {"kind": "random"}):
        with pytest.raises(ValueError):
            replace(valid, **changes)


def test_bindings_require_pinned_hashes_and_compatible_eras():
    s = api()
    pin = s.PackagePin("cosmere.core", "1.0.0", "a" * 64)
    binding = s.CosmereRulesBinding(pin, (s.PackagePin("cosmere.mistborn.era-1", "1.0.0", "b" * 64),),
                                    "scadrial", ("era-1",))
    assert binding.core_package.package_hash == "a" * 64
    for changes in ({"default_world": "mars"}, {"default_world": "roshar"},
                    {"allowed_eras": ("era-3",)}, {"enabled_packs": (pin,)},
                    {"enabled_packs": binding.enabled_packs * 2}):
        with pytest.raises(ValueError):
            replace(binding, **changes)
    with pytest.raises(ValueError):
        s.PackagePin("cosmere.core", "1.0.0", "bad")


def test_advancement_validates_revision_level_and_freezes_choices():
    s = api()
    event = s.AdvancementEvent("cosmere.event.one", "cosmere.actor.one", "advance", 0, 1,
                              1, 2, "a" * 64, {"skills": ["athletics"]}, ())
    assert event.choices["skills"] == ("athletics",)
    for changes in ({"revision": 0}, {"prior_revision": -1}, {"to_level": 3},
                    {"action": "restore"}, {"package_hash": "x"}):
        with pytest.raises(ValueError):
            replace(event, **changes)
    reverted = replace(event, action="revert", from_level=2, to_level=1)
    assert reverted.to_level == 1


def test_advancement_accepts_runtime_actor_ids_and_carries_inverse_audit_data():
    s = api()
    event = s.AdvancementEvent(
        "9f4f532c3dde4dbf84b070836750c3c5",
        "3520ca4cb56243f095ec8a5209174a68",
        "advance", 7, 8, 4, 5, "a" * 64,
        {"operations": [{"path": ["build", "level"], "before": 4, "after": 5}]},
        (),
        prior_snapshot={"level": 4},
        resulting_snapshot={"level": 5},
        audit_metadata={"request_id": "9f4f532c3dde4dbf84b070836750c3c5"},
    )
    assert event.actor_id == "3520ca4cb56243f095ec8a5209174a68"
    assert event.prior_snapshot["level"] == 4
    with pytest.raises(TypeError):
        event.audit_metadata["request_id"] = "changed"


def test_effects_validate_attribute_stacking_and_duration():
    s = api()
    effect = s.EffectInstance("cosmere.effect.one", "cosmere.core.test", "cosmere.actor.one",
                              {}, "strength", "cosmere.stack.one", "round", 2,
                              "turn_end", "refresh")
    assert effect.remaining_duration == 2
    for changes in ({"selected_attribute": "dexterity"}, {"duration_unit": "minute-ish"},
                    {"remaining_duration": -1}, {"trigger": "random"},
                    {"refresh_policy": "random"}, {"stacking_identity": "bare"}):
        with pytest.raises(ValueError):
            replace(effect, **changes)


def test_adventures_reject_core_overrides():
    s = api()
    kwargs = dict(id="cosmere.adventure.one", version="1.0.0", pack_id="cosmere.stonewalkers",
                  sources=("stormlight.stonewalkers",), worlds=("roshar",), eras=(),
                  spoiler_scope="gm", audit_status="provisional", records=(rule(overrides="cosmere.core.test"),))
    with pytest.raises(ValueError, match="override"):
        s.AdventureManifest(**kwargs)


@pytest.mark.parametrize("changes", [
    {"parameters": {"C:\\private\\source.pdf": "hidden in a key"}},
    {"parameters": {"/home/private/source.pdf": "hidden in a key"}},
    {"summary": "Read the source at /home/private/source.pdf for details."},
    {"parameters": {"note": "Local source: /tmp/private/source.pdf"}},
    {"summary": "Read \\\\private-server\\books\\source.pdf for details."},
])
def test_paths_in_keys_or_embedded_prose_cannot_enter_normalized_records(changes):
    with pytest.raises(ValueError, match="absolute paths"):
        rule(**changes)


@pytest.mark.parametrize("changes", [
    {"summary": "Read the local source at /private-source.pdf for details."},
    {"parameters": {"nested": [{"note": "The source is /private-source.pdf."}]}},
])
def test_embedded_root_level_absolute_filenames_are_rejected(changes):
    with pytest.raises(ValueError, match="absolute paths"):
        rule(**changes)


@pytest.mark.parametrize("payload", [
    {"page_images": ["data:image/png;base64,..."]},
    {"page_image": "encoded image"},
    {"pageImages": ["encoded image"]},
    {"page-images": ["encoded image"]},
    {"extracted_prose": "synthetic copied-source fixture"},
    {"source_text": "synthetic copied-source fixture"},
    {"source_binary": [37, 80, 68, 70]},
    {"pdf_bytes": [37, 80, 68, 70]},
    {"binary_payload": "encoded source"},
    {"customer_watermark": "synthetic watermark fixture"},
    {"note": "data:image/png;base64,..."},
    {"note": "Embedded data:application/pdf;base64,JVBERi0xLjQ="},
    {"note": "data:audio/wav;base64,..."},
    {"note": "data:video/mp4;base64,..."},
    {"note": "data:application/octet-stream;base64,..."},
    {"note": "Encoded PNG: iVBORw0KGgo..."},
    {"note": "Encoded PDF: JVBERi0xLjQ="},
    {"note": "%PDF-1.7 synthetic source fixture"},
])
def test_nested_source_and_media_payloads_are_rejected(payload):
    with pytest.raises(ValueError, match="source|media"):
        rule(parameters={"nested": [{"payload": payload}]})


@pytest.mark.parametrize("field", [" PAGE_IMAGES ", "page__images"])
def test_prohibited_payload_field_aliases_cannot_bypass_normalization(field):
    with pytest.raises(ValueError, match="source"):
        rule(parameters={"nested": {field: ["synthetic encoded image"]}})
