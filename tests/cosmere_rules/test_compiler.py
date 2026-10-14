"""Compile real normalized data and reject corrupt or incomplete packages."""
import copy
import hashlib
import importlib
import json

import pytest


def compiler():
    return importlib.import_module("systems.cosmere.rules.compiler")


def authoring():
    return {"schema_version": 1, "artifacts": [dict(
        product_id="mistborn.handbook", basename="book.pdf", edition="v1.02", sha256="a" * 64,
        byte_count=100, page_count=410, authority_tier="handbook", extraction_class="rules",
        spoiler_scope="player", rights_state="private")], "packs": [dict(
        id="cosmere.core", version="1.0.0", kind="core", dependencies=[], sources=["mistborn.handbook"],
        worlds=["cosmere"], eras=[], spoiler_scope="player", audit_status="provisional", records=[dict(
            id="cosmere.core.focus", version="1.0.0", kind="resource", name="Focus",
            summary="Original summary", citations=[dict(artifact_id="mistborn.handbook", page_start=1, page_end=2)],
            worlds=["cosmere"], eras=[], spoiler_scope="player", automation="tracked",
            audit_status="provisional", parameters={"max": 2})])]}


def test_canonical_utf8_json_and_hash_ignore_only_own_hash():
    c = compiler()
    literal = '{"a":1,"z":"é"}\n'.encode("utf-8")
    assert c.canonical_json({"z": "é", "a": 1}) == literal
    assert c.package_hash({"z": "é", "a": 1, "package_hash": "ignored"}) == hashlib.sha256(literal).hexdigest()
    assert c.package_hash({"a": 1, "z": "é"}) == c.package_hash({"z": "é", "a": 1})
    assert c.package_hash({"a": {"package_hash": "nested"}}) != c.package_hash({"a": {}})


def test_compile_load_round_trip_is_deeply_immutable(tmp_path):
    c = compiler()
    raw = c.compile_registry(authoring())
    path = tmp_path / "registry.json"
    path.write_bytes(raw)
    r = importlib.import_module("systems.cosmere.rules.registry").load_registry(path)
    assert r.records["cosmere.core.focus"].parameters["max"] == 2
    assert r.packs["cosmere.core"].sources == ("mistborn.handbook",)
    with pytest.raises(TypeError):
        r.packs["cosmere.core"] = None
    with pytest.raises(TypeError):
        r.records["cosmere.core.focus"].parameters["max"] = 5
    assert json.loads(raw)["package_hash"] == r.package_hash


def test_every_version_is_table_validated_not_only_the_legacy_id_view():
    data = authoring()
    newer = copy.deepcopy(data["packs"][0])
    newer["version"] = "1.1.0"
    newer["records"] = [dict(
        id="cosmere.core.broken-table", version="1.1.0", kind="table",
        name="Broken table", summary="Synthetic invalid nested table.",
        citations=[dict(artifact_id="mistborn.handbook", page_start=1, page_end=1)],
        worlds=["cosmere"], eras=[], spoiler_scope="player",
        automation="calculated", audit_status="provisional",
        parameters={"results": [{"id": "row", "weight": 1,
                                  "table_id": "cosmere.core.missing"}]})]
    data["packs"].append(newer)
    with pytest.raises(ValueError, match="missing nested table"):
        compiler().compile_registry(data)


@pytest.mark.parametrize("mutation", ["artifact_duplicate", "pack_duplicate", "record_duplicate", "unknown_dependency",
    "dependency_cycle", "unknown_source", "unknown_citation", "page_overflow", "missing_citation",
    "unlisted_citation", "verified_quarantine", "verified_quarantine_rule", "adventure_override",
    "world_mismatch", "era_mismatch", "spoiler_mismatch", "unknown_field", "unsupported_schema", "absolute_path"])
def test_compiler_fails_closed(mutation):
    data = authoring()
    pack = data["packs"][0]
    record = pack["records"][0]
    source = data["artifacts"][0]
    if mutation == "artifact_duplicate":
        data["artifacts"].append(copy.deepcopy(source))
    elif mutation == "pack_duplicate":
        data["packs"].append(copy.deepcopy(pack))
    elif mutation == "record_duplicate":
        other = copy.deepcopy(pack)
        other.update(id="cosmere.other", kind="world", dependencies=["cosmere.core"])
        data["packs"].append(other)
    elif mutation == "unknown_dependency":
        pack["dependencies"] = ["cosmere.missing"]
    elif mutation == "dependency_cycle":
        other = copy.deepcopy(pack)
        other.update(id="cosmere.other", records=[], dependencies=["cosmere.core"])
        pack["dependencies"] = ["cosmere.other"]
        data["packs"].append(other)
    elif mutation == "unknown_source":
        pack["sources"] = ["mistborn.missing"]
    elif mutation == "unknown_citation":
        record["citations"][0]["artifact_id"] = "mistborn.missing"
    elif mutation == "page_overflow":
        record["citations"][0]["page_end"] = 411
    elif mutation == "missing_citation":
        record["citations"] = []
    elif mutation == "unlisted_citation":
        other = copy.deepcopy(source)
        other["product_id"] = "mistborn.other"
        data["artifacts"].append(other)
        record["citations"][0]["artifact_id"] = "mistborn.other"
    elif mutation.startswith("verified_quarantine"):
        source["rights_state"] = "quarantined"
        (record if mutation.endswith("_rule") else pack)["audit_status"] = "verified"
    elif mutation == "adventure_override":
        pack["kind"] = "adventure"
        record["overrides"] = "cosmere.core.other"
    elif mutation == "world_mismatch":
        pack["worlds"] = ["roshar"]
        record["worlds"] = ["scadrial"]
    elif mutation == "era_mismatch":
        pack["worlds"] = record["worlds"] = ["scadrial"]
        pack["eras"] = ["era-1"]
        record["eras"] = ["era-2"]
    elif mutation == "spoiler_mismatch":
        record["spoiler_scope"] = "gm"
    elif mutation == "unknown_field":
        data["extracted_prose"] = "private"
    elif mutation == "unsupported_schema":
        data["schema_version"] = 2
    elif mutation == "absolute_path":
        record["parameters"]["file"] = "/home/private/source.pdf"
    with pytest.raises(ValueError):
        compiler().compile_registry(data)


@pytest.mark.parametrize("mutation", ["root_hash", "pack_hash", "payload", "duplicate_json_key", "expected_hash"])
def test_registry_rejects_tampering_and_wrong_trusted_binding(tmp_path, mutation):
    c = compiler()
    data = json.loads(c.compile_registry(authoring()))
    expected = data["package_hash"]
    if mutation == "root_hash":
        data["package_hash"] = "b" * 64
    elif mutation == "pack_hash":
        data["packs"][0]["package_hash"] = "b" * 64
    elif mutation == "payload":
        data["packs"][0]["records"][0]["parameters"]["max"] = 99
    elif mutation == "expected_hash":
        expected = "b" * 64
    raw = json.dumps(data).encode("utf-8")
    if mutation == "duplicate_json_key":
        raw = b'{"schema_version":1,"schema_version":2}'
    path = tmp_path / "registry.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        importlib.import_module("systems.cosmere.rules.registry").load_registry(path, expected_hash=expected)


def test_registry_resolves_exact_pins_and_requires_enabled_dependencies(tmp_path):
    c = compiler()
    data = authoring()
    other = copy.deepcopy(data["packs"][0])
    other.update(id="cosmere.mistborn.era-1", kind="world", worlds=["scadrial"], eras=["era-1"],
                 dependencies=["cosmere.core"], records=[])
    data["packs"].append(other)
    path = tmp_path / "registry.json"
    path.write_bytes(c.compile_registry(data))
    r = importlib.import_module("systems.cosmere.rules.registry").load_registry(path)
    s = importlib.import_module("systems.cosmere.rules.schema")
    pins = {key: s.PackagePin(pack.id, pack.version, pack.package_hash) for key, pack in r.packs.items()}
    binding = s.CosmereRulesBinding(pins["cosmere.core"], (pins[other["id"]],), "scadrial", ("era-1",))
    assert tuple(p.id for p in r.resolve_binding(binding)) == ("cosmere.core", other["id"])
    with pytest.raises(ValueError):
        r.resolve_pin(s.PackagePin("cosmere.core", "1.0.0", "b" * 64))
    with pytest.raises(ValueError):
        r.resolve_pin(s.PackagePin("cosmere.core", "1.1.0", pins["cosmere.core"].package_hash))
    with pytest.raises(ValueError):
        r.resolve_binding(s.CosmereRulesBinding(pins[other["id"]], (), "scadrial", ("era-1",)))


def test_binding_can_enable_both_mistborn_eras_without_one_pack_claiming_both():
    from pathlib import Path
    r = importlib.import_module("systems.cosmere.rules.registry").load_registry(
        Path(__file__).resolve().parents[1] / "fixtures" / "cosmere_rules" / "rules" / "registry.json")
    s = importlib.import_module("systems.cosmere.rules.schema")
    pins = {key: s.PackagePin(pack.id, pack.version, pack.package_hash) for key, pack in r.packs.items()}
    binding = s.CosmereRulesBinding(pins["cosmere.core"],
        (pins["cosmere.mistborn.core"], pins["cosmere.mistborn.era-1"],
         pins["cosmere.mistborn.era-2"]), "scadrial", ("era-1", "era-2"))
    assert len(r.resolve_binding(binding)) == 4
    with pytest.raises(ValueError):
        r.resolve_binding(s.CosmereRulesBinding(pins["cosmere.core"],
            (pins["cosmere.mistborn.core"], pins["cosmere.mistborn.era-1"]),
            "scadrial", ("era-2",)))


@pytest.mark.parametrize("changes", [{"sha256": "b" * 64}, {"edition": "v1.03"}, {"rights_state": "quarantined"}])
def test_pinned_pack_hash_changes_when_its_source_artifact_facts_change(changes):
    c = compiler()
    data = authoring()
    original = json.loads(c.compile_registry(data))["packs"][0]["package_hash"]
    data["artifacts"][0].update(changes)
    changed = json.loads(c.compile_registry(data))["packs"][0]["package_hash"]
    assert changed != original


def test_compiler_rejects_embedded_root_level_absolute_filename():
    data = authoring()
    data["packs"][0]["records"][0]["summary"] = "Read the local source at /private-source.pdf for details."
    with pytest.raises(ValueError, match="absolute paths"):
        compiler().compile_registry(data)


def test_compiler_rejects_nested_extracted_prose():
    data = authoring()
    data["packs"][0]["records"][0]["parameters"] = {"nested": [{"extracted_prose": "synthetic copied-source fixture"}]}
    with pytest.raises(ValueError, match="source"):
        compiler().compile_registry(data)


@pytest.mark.parametrize("pack_scope,record_scope", [("none", "player"), ("none", "gm"), ("player", "gm")])
def test_spoiler_hierarchy_blocks_records_above_pack_scope(pack_scope, record_scope):
    data = authoring()
    data["packs"][0]["spoiler_scope"] = pack_scope
    data["packs"][0]["records"][0]["spoiler_scope"] = record_scope
    with pytest.raises(ValueError, match="spoiler scope"):
        compiler().compile_registry(data)


@pytest.mark.parametrize("pack_scope,record_scope", [("none", "none"), ("player", "none"), ("player", "player"),
                                                       ("gm", "none"), ("gm", "player"), ("gm", "gm")])
def test_spoiler_hierarchy_accepts_records_at_or_below_pack_scope(pack_scope, record_scope):
    data = authoring()
    data["packs"][0]["spoiler_scope"] = pack_scope
    data["packs"][0]["records"][0]["spoiler_scope"] = record_scope
    assert json.loads(compiler().compile_registry(data))["packs"][0]["records"][0]["spoiler_scope"] == record_scope


def _table_record(record_id, rows):
    record = copy.deepcopy(authoring()["packs"][0]["records"][0])
    record.update(id=record_id, kind="table", name=record_id,
                  parameters={"results": rows})
    return record


@pytest.mark.parametrize("mutation,message", [
    ("invalid_weight", "positive integer"),
    ("missing_nested", "missing nested table"),
    ("cycle", "cycle"),
    ("undeclared_dependency", "dependency"),
])
def test_compiler_rejects_invalid_table_records_and_reference_graph(mutation, message):
    data = authoring()
    pack = data["packs"][0]
    if mutation == "invalid_weight":
        pack["records"] = [_table_record(
            "cosmere.core.invalid", [{"id": "bad", "weight": 0,
                                      "value": {"text": "Bad"}}])]
    elif mutation == "missing_nested":
        pack["records"] = [_table_record(
            "cosmere.core.root", [{"id": "missing", "weight": 1,
                                   "table_id": "cosmere.core.absent"}])]
    elif mutation == "cycle":
        pack["records"] = [
            _table_record("cosmere.core.one", [
                {"id": "two", "weight": 1, "table_id": "cosmere.core.two"}]),
            _table_record("cosmere.core.two", [
                {"id": "one", "weight": 1, "table_id": "cosmere.core.one"}]),
        ]
    else:
        pack["records"] = [_table_record(
            "cosmere.core.root", [{"id": "addon", "weight": 1,
                                   "table_id": "cosmere.addon.leaf"}])]
        addon = copy.deepcopy(pack)
        addon.update(id="cosmere.addon", kind="world",
                     dependencies=["cosmere.core"], records=[_table_record(
                         "cosmere.addon.leaf", [{"id": "done", "weight": 1,
                                                 "value": {"text": "Done"}}])])
        data["packs"].append(addon)

    with pytest.raises(ValueError, match=message):
        compiler().compile_registry(data)
