"""M0 corrections to batch 1 (handoff 017 findings, handoff 018 ownership).

Run against c5653e05, 12 of these fail: S5/uniqueItems, the 2,048-token timing
guard, (kind, id) identity, the bounds lint and the label exclusions. The
entity-cap, 2 MiB and valid-label cases pin behaviour that already held, so the
corrected batch cannot lose it."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from vtt.protocol import ContractRegistry, ProtocolValidationError, StrictJSON
from vtt.protocol.semantics import SemanticError, check_frame

ROOT = Path(__file__).resolve().parents[3] / "contracts" / "vtt" / "v1"
SUB = "sub_AAAAAAAAAAAAAAAA"
VER = "ver_AAAAAAAAAAAAAAAA"


def _token(i: int, **extra):
    return {"kind": "token", "id": f"token_{i}", "version": VER, "x": 64, "y": 64, "size": 1,
            "label": "Valeros", "color": 13214542, "artAssetId": None, "actorId": None, **extra}


def _table_snapshot(entities):
    return {"schemaVersion": 1, "frame": "snapshot", "audience": "table", "subscriptionId": SUB, "seq": 0,
            "campaignId": "campaign_a", "sceneId": "scene_a", "principal": {"kind": "table"},
            "capabilities": [], "entities": entities}


def _gm_snapshot(entities):
    return {"schemaVersion": 1, "frame": "snapshot", "audience": "gm", "subscriptionId": SUB, "seq": 0,
            "campaignId": "campaign_a", "sceneId": "scene_a",
            "principal": {"kind": "member", "id": "user_gm", "campaignRole": "gm"},
            "capabilities": ["scene.prepare"], "entities": entities}


# --- S5: no uniqueItems; identity by (kind, id) in code -----------------------------------

def test_no_schema_uses_unique_items_and_the_keyword_is_refused(tmp_path: Path):
    for path in (ROOT / "schemas").glob("*.schema.json"):
        assert "uniqueItems" not in path.read_text(encoding="utf-8"), path.name
    shutil.copytree(ROOT, tmp_path / "v1")
    path = tmp_path / "v1" / "schemas" / "table.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    schema["definitions"]["Snapshot"]["properties"]["entities"]["uniqueItems"] = True
    path.write_text(json.dumps(schema), encoding="utf-8")
    with pytest.raises(ProtocolValidationError):
        ContractRegistry(tmp_path / "v1")


def test_a_maximum_table_snapshot_validates_quickly():
    # 2,048 tokens: 886 ms in Ajv with uniqueItems (handoff 017). Python's jsonschema
    # is slower than Ajv; the bound guards against a quadratic check coming back.
    #
    # It measures scaling, not wall time. An absolute bound (it was 2 s) failed
    # intermittently on a loaded machine with the code unchanged (2.36 s once in
    # ten runs, 2026-10-16). Quadrupling the tokens costs about 4x when linear and
    # about 16x when quadratic; the best of three runs at each size filters noise.
    #
    # The scaling is measured on the frame rules alone, because that is where a
    # quadratic uniqueness check would live (the schemas carry no uniqueItems).
    # Timed together with jsonschema's own linear cost, a pairwise check was
    # swamped and passed (checked by mutation).
    from vtt.protocol.semantics import check_frame
    registry = ContractRegistry()

    def best(n):
        frame = _table_snapshot([_token(i) for i in range(n)])
        times = []
        for _ in range(5):
            start = time.perf_counter()
            check_frame("table", frame)
            times.append(time.perf_counter() - start)
        return min(times)

    small, large = best(512), best(2048)
    assert large / small < 9, (small, large)
    # and the whole validation stays far from pathological
    frame = _table_snapshot([_token(i) for i in range(2048)])
    start = time.perf_counter()
    registry.validate("table", frame)
    assert time.perf_counter() - start < 10.0


def test_same_key_with_different_fields_is_a_duplicate():
    frame = _table_snapshot([_token(1), _token(1, x=128)])
    with pytest.raises(ProtocolValidationError):
        ContractRegistry().validate("table", frame)


def test_entity_caps_hold_for_both_audiences():
    registry = ContractRegistry()
    with pytest.raises(ProtocolValidationError):
        registry.validate("table", _table_snapshot([_token(i) for i in range(2049)]))
    gm_tokens = [_token(i, hidden=False, locked=False, controllerIds=[]) for i in range(4097)]
    with pytest.raises(ProtocolValidationError):
        registry.validate("gm", _gm_snapshot(gm_tokens))
    registry.validate("gm", _gm_snapshot(gm_tokens[:4096]))


# --- finding 3: the 2 MiB byte cap governs a schema-valid frame -----------------------------

def test_a_schema_valid_gm_frame_over_two_mib_cannot_be_sent():
    controllers = [f"user_{'x' * 100}{n:02d}" for n in range(64)]
    tokens = [_token(i, hidden=False, locked=False, controllerIds=controllers) for i in range(400)]
    frame = _gm_snapshot(tokens)
    ContractRegistry().validate("gm", frame)          # the schema allows it ...
    with pytest.raises(ProtocolValidationError):      # ... the transport cap does not
        StrictJSON.dumps(frame)


# --- finding 7: bounds lint covers strings, arrays and numbers -------------------------------

@pytest.mark.parametrize("mutate", [
    lambda d: d["Label"].pop("maxLength"),
    lambda d: d["TableScene"]["properties"]["width"].pop("maximum"),
])
def test_unbounded_common_shapes_are_refused(tmp_path: Path, mutate):
    shutil.copytree(ROOT, tmp_path / "v1")
    path = tmp_path / "v1" / "schemas" / "common.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    mutate(schema["definitions"])
    path.write_text(json.dumps(schema), encoding="utf-8")
    with pytest.raises(ProtocolValidationError):
        ContractRegistry(tmp_path / "v1")


def test_an_unbounded_array_is_refused(tmp_path: Path):
    shutil.copytree(ROOT, tmp_path / "v1")
    path = tmp_path / "v1" / "schemas" / "table.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    schema["definitions"]["Patch"]["properties"]["removed"].pop("maxItems")
    path.write_text(json.dumps(schema), encoding="utf-8")
    with pytest.raises(ProtocolValidationError):
        ContractRegistry(tmp_path / "v1")


# --- finding 4: display text ------------------------------------------------------------------

@pytest.mark.parametrize("label,ok", [
    ("Valeros", True), ("Valéros", True), ("\U0001d11e Bard", True),
    ("Vale\u0000ros", False), ("Vale\u0085ros", False), ("‮sorelaV", False),
    ("Vale⁦ros", False), ("Vale\ud800ros", False), ("line\nbreak", False),
])
def test_table_labels_exclude_controls_bidi_and_lone_surrogates(label, ok):
    frame = _table_snapshot([_token(1, label=label)])
    if ok:
        ContractRegistry().validate("table", frame)
    else:
        with pytest.raises(ProtocolValidationError):
            ContractRegistry().validate("table", frame)


# --- finding 10: rules a schema cannot express ------------------------------------------------

def test_frame_rules_directly():
    scene = {"kind": "scene", "id": "scene_a"}
    check_frame("table", _table_snapshot([scene, _token(1)]))
    for bad in (
        _table_snapshot([scene, dict(scene, id="scene_b")]),
        dict(_table_snapshot([_token(1)]), sceneId=None),
        {"frame": "patch", "upserted": [_token(1)], "removed": [{"kind": "token", "id": "token_1"}]},
    ):
        with pytest.raises(SemanticError):
            check_frame("table", bad)
    # a removed key and an upserted key of a different kind with the same id are distinct
    check_frame("table", {"frame": "patch", "upserted": [_token(1)], "removed": [{"kind": "light", "id": "token_1"}]})
