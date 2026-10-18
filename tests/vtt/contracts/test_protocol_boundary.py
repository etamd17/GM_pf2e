"""Wire boundary regressions; removing strict decoding must fail these tests."""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def boundary():
    try:
        return importlib.import_module("vtt.protocol")
    except ModuleNotFoundError:
        pytest.fail("The canonical VTT protocol boundary is not implemented yet")


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_non_finite_json_is_rejected_before_schema_validation(raw):
    api = boundary()
    with pytest.raises(api.ProtocolValidationError):
        api.StrictJSON.loads(raw)


@pytest.mark.parametrize(
    "raw",
    ['{"seq":1,"seq":2}', '{"a":{"seq":1,"\\u0073eq":2}}'],
)
def test_duplicate_keys_are_rejected_including_decoded_nested_keys(raw):
    api = boundary()
    with pytest.raises(api.ProtocolValidationError):
        api.StrictJSON.loads(raw)


def test_same_json_module_is_usable_by_socketio_without_weakening_hooks():
    api = boundary()
    value = {"schemaVersion": 1, "label": "A\U0001f409"}
    encoded = api.StrictJSON.dumps(value, separators=(",", ":"))
    assert api.StrictJSON.loads(encoded) == value
    with pytest.raises(api.ProtocolValidationError):
        api.StrictJSON.dumps({"x": float("inf")}, allow_nan=True)
    with pytest.raises(api.ProtocolValidationError):
        api.StrictJSON.loads('{"seq":1,"seq":2}', object_pairs_hook=dict)
    with pytest.raises(api.ProtocolValidationError):
        api.StrictJSON.loads('"literal\nnewline"', strict=False)


def test_outbound_decoded_non_finite_values_are_rejected():
    api = boundary()
    for value in (float("inf"), float("nan")):
        with pytest.raises(api.ProtocolValidationError):
            api.ContractRegistry().validate("table", {"x": value})


def test_registry_fails_closed_for_reserved_events_and_audiences():
    api = boundary()
    registry = api.ContractRegistry()
    assert registry.schema_for_event("vtt.snapshot", "table") == "table"
    # commands-1 makes vtt.command ready for the GM only; a table has no command schema.
    assert registry.schema_for_event("vtt.command", "gm") == "command"
    # beta-1 makes vtt.transient ready for the GM only; a table never sends one.
    assert registry.schema_for_event("vtt.transient", "gm") == "transient-send"
    assert registry.schema_for_event("vtt.transient_relay", "table") == "transient"
    for event, audience in (("vtt.command", "table"), ("vtt.transient", "table"), ("vtt.whisper.send", "gm"),
                            ("vtt.snapshot", "player"), ("bogus", "gm")):
        with pytest.raises(api.ProtocolValidationError):
            registry.schema_for_event(event, audience)


def test_table_event_schema_cannot_accept_a_forged_gm_subscription():
    api = boundary()
    registry = api.ContractRegistry()
    key = registry.schema_for_event("vtt.subscribe", "table")
    with pytest.raises(api.ProtocolValidationError):
        registry.validate(key, {"schemaVersion": 1, "audience": "gm", "campaignId": "campaign_secret", "sceneId": "scene_secret"})


def test_shared_valid_and_invalid_fixtures_match_their_declared_results():
    api = boundary()
    registry = api.ContractRegistry()
    cases = json.loads((registry.root / "fixtures" / "cases.json").read_text(encoding="utf-8"))
    assert len(cases) >= 40, "The published corpus must cover all closed reason and ack enums"
    mismatches = []
    for case in cases:
        try:
            if "raw" in case:
                registry.parse_and_validate(case["schema"], case["raw"])
            else:
                registry.validate(case["schema"], case["message"])
            valid = True
        except api.ProtocolValidationError:
            valid = False
        if valid != case["valid"]:
            mismatches.append(case["id"])
    assert mismatches == []


def test_table_schema_refuses_gm_state_and_command_correlation():
    api = boundary()
    registry = api.ContractRegistry()
    cases = json.loads((registry.root / "fixtures" / "cases.json").read_text(encoding="utf-8"))
    message = next(c["message"] for c in cases if c["id"] == "table-token-snapshot")
    message["entities"][0]["hidden"] = True
    with pytest.raises(api.ProtocolValidationError):
        registry.validate("table", message)
    patch = {
        "schemaVersion": 1, "frame": "patch", "subscriptionId": "sub_AAAAAAAAAAAAAAAA",
        "seq": 1, "upserted": [], "removed": [], "originCommandId": "cmd_AAAAAAAAAAAAAAAA",
    }
    with pytest.raises(api.ProtocolValidationError):
        registry.validate("table", patch)


def test_an_external_schema_reference_is_rejected_without_network_access(tmp_path: Path):
    api = boundary()
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "bad.schema.json").write_text(json.dumps({
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": "https://vtt.invalid/v1/bad.schema.json",
        "$ref": "https://remote.example.invalid/schema.json",
    }), encoding="utf-8")
    (tmp_path / "registry.json").write_text(json.dumps({
        "schemaVersion": 1, "schemas": {"bad": "schemas/bad.schema.json"}, "events": {},
    }), encoding="utf-8")
    with pytest.raises(api.ProtocolValidationError):
        api.ContractRegistry(tmp_path)


def test_decoded_depth_and_raw_byte_limits_cannot_be_bypassed():
    api = boundary()
    value = None
    for _ in range(70):
        value = [value]
    with pytest.raises(api.ProtocolValidationError):
        api.StrictJSON.dumps(value)
    with pytest.raises(api.ProtocolValidationError):
        api.StrictJSON.loads('"' + 'x' * 2_097_152 + '"')


def test_schema_digest_is_independent_of_checkout_line_endings(tmp_path: Path):
    api = boundary()
    original = api.ContractRegistry()
    shutil.copytree(original.root, tmp_path / "v1")
    for path in (tmp_path / "v1").rglob("*.json"):
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert api.ContractRegistry(tmp_path / "v1").source_digest() == original.source_digest()


def test_schema_digest_changes_when_a_validation_bound_changes(tmp_path: Path):
    api = boundary()
    original = api.ContractRegistry()
    shutil.copytree(original.root, tmp_path / "v1")
    path = tmp_path / "v1" / "schemas" / "common.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    schema["definitions"]["Label"]["maxLength"] = 79
    path.write_text(json.dumps(schema), encoding="utf-8")
    assert api.ContractRegistry(tmp_path / "v1").source_digest() != original.source_digest()


def test_unknown_schema_keywords_fail_instead_of_being_silently_ignored(tmp_path: Path):
    api = boundary()
    shutil.copytree(api.ContractRegistry().root, tmp_path / "v1")
    path = tmp_path / "v1" / "schemas" / "table.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    schema["unexpectedStructuralKeyword"] = {"secret": "ignored"}
    path.write_text(json.dumps(schema), encoding="utf-8")
    with pytest.raises(api.ProtocolValidationError):
        api.ContractRegistry(tmp_path / "v1")


def test_javascript_fixture_runner_is_published_with_the_canonical_batch():
    root = Path(__file__).resolve().parents[3]
    runner = root / "tools" / "vtt" / "check-contracts.mjs"
    assert runner.is_file(), "The independently runnable JavaScript agreement runner is missing"
    result = subprocess.run(["node", str(runner), "--help"], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert "--client-root" in result.stdout


def test_python_fixture_runner_handles_invalid_examples_without_rejecting_the_manifest():
    result = subprocess.run([sys.executable, "-m", "vtt.protocol.check", "--json"], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert len(report["results"]) >= 40
    assert all(case["matches"] for case in report["results"])
