"""MB-11: the faster registry load keeps every check and every byte.

The ``_old_*`` functions are verbatim copies of the implementations MB-11
replaced (``schema.freeze``, ``schema.to_dict`` and ``compiler.canonical_json``
at ce77d7e8). They are the oracle: every accept/reject decision, every error
message and every serialized byte must match them.
"""
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import IntEnum
import json
import math
from pathlib import Path
import re
from types import MappingProxyType

import pytest

from systems.cosmere.rules import compiler, registry, schema
from systems.cosmere.rules.compiler import MAX_JSON_BYTES, canonical_json, package_hash
from systems.cosmere.rules.schema import (FORBIDDEN_KEYS, MEDIA_PAYLOAD, CosmereRulesBinding,
                                          PackagePin, RulesValidationError, check_json, freeze,
                                          require, text, to_dict)
from systems.cosmere.rules.seeds import BOOTSTRAP_REGISTRY

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "cosmere_rules"


def _old_freeze(value, depth=0):
    require(depth <= 32, "JSON nesting limit exceeded")
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        require(math.isfinite(value), "invalid JSON number")
        return value
    if isinstance(value, str):
        require(MEDIA_PAYLOAD.search(value) is None, "source/media payload is prohibited")
        require(not (value.startswith(("/", "\\", "~/")) or re.search(r"[A-Za-z]:[\\/]", value)
                     or re.search(r"(?<![\w:/])/[^\s]+", value)
                     or re.search(r"(?<!\w)\\\\[^\\\s]+\\", value)),
                "absolute paths are runtime input only")
        return value
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            text(key, "JSON key")
            _old_freeze(key, depth + 1)
            normalized_key = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
            normalized_key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", normalized_key)
            normalized_key = re.sub(r"[-_\s]+", "_", normalized_key).strip("_").lower()
            require(normalized_key not in FORBIDDEN_KEYS, "raw source content is prohibited")
            result[key] = _old_freeze(item, depth + 1)
        return MappingProxyType(result)
    if isinstance(value, (list, tuple)):
        return tuple(_old_freeze(item, depth + 1) for item in value)
    raise RulesValidationError("invalid normalized JSON value")


def _old_to_dict(value):
    if isinstance(value, CosmereRulesBinding):
        result = {field.name: _old_to_dict(getattr(value, field.name)) for field in fields(value)}
        if value.allowed_worlds == (value.default_world,):
            result.pop("allowed_worlds", None)
        return result
    if is_dataclass(value):
        return {field.name: _old_to_dict(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _old_to_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_old_to_dict(item) for item in value]
    return value


def _old_canonical_json(value):
    try:
        normalized = _old_to_dict(_old_freeze(_old_to_dict(value)))
        result = (json.dumps(normalized, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (UnicodeError, RecursionError):
        raise RulesValidationError("invalid normalized JSON") from None
    require(len(result) <= MAX_JSON_BYTES, "JSON size limit exceeded")
    return result


class _Level(IntEnum):
    ONE = 1


def _nested(depth, leaf):
    value = leaf
    for _ in range(depth):
        value = [value]
    return value


def _mapping_at(depth, mapping):
    value = mapping
    for _ in range(depth):
        value = [value]
    return value


GOOD = [
    None, True, 0, -7, 2.5, "", "plain text", "é and 中文", "a/b relative",
    "ratio 1/2", [], (), {}, MappingProxyType({"a": (1, 2)}),
    {"Name": "x", "camelCase": [1, {"deep": None}], "with space": "ok"},
    _nested(32, "leaf"), _mapping_at(31, {"key": "value"}),
]

BAD = [
    "data:image/png;base64,AAAA", "%PDF-1.7", "x iVBORw0KGgo", "/etc/passwd", "\\server\\share",
    "~/notes", "C:\\Users\\x", "see /usr/lib here", "a \\\\host\\share\\",
    "http://example.com/x",         # "p:/" reads as a drive path, as it always has
    {"raw_text": "x"}, {"RawText": "x"}, {"PDFBytes": 1}, {"source-text": 1}, {"Local Path": 1},
    {"": 1}, {"   ": 1}, {1: "x"}, float("nan"), float("inf"), _nested(33, "leaf"),
    _mapping_at(32, {"key": "value"}), _mapping_at(31, {"/abs": 1}), {1, 2}, object(),
    _Level.ONE, b"bytes", {"good": "/abs", "raw_text": 1}, {"raw_text": "/abs"},
    [1, "ok", ["fine", {"x": "C:/drive"}]],
]


def _outcome(function, value):
    try:
        return ("ok", function(value))
    except RulesValidationError as error:
        return ("error", str(error))


@pytest.mark.parametrize(("value", "verdict"), [(v, "ok") for v in GOOD] + [(v, "error") for v in BAD])
def test_freeze_and_check_json_decide_exactly_like_the_old_freeze(value, verdict):
    expected = _outcome(_old_freeze, value)
    assert expected[0] == verdict, expected      # the corpus means what it says
    for attempt in range(2):        # a remembered pass must never hide a failure
        got = _outcome(freeze, value)
        assert got[0] == expected[0], (attempt, got, expected)
        if expected[0] == "error":
            assert got == expected
            assert _outcome(check_json, value) == expected
        else:
            assert _old_to_dict(got[1]) == _old_to_dict(expected[1])
            assert type(got[1]) is type(expected[1])
            assert _outcome(check_json, value) == ("ok", None)


def test_a_string_remembered_as_a_value_is_still_checked_as_a_key():
    freeze(["raw_text", "Local Path"])
    with pytest.raises(RulesValidationError, match="raw source content is prohibited"):
        freeze({"raw_text": 1})
    with pytest.raises(RulesValidationError, match="raw source content is prohibited"):
        check_json({"Local Path": 1})


def _remembered():
    return len(schema._PASSED_TEXT) + len(schema._PASSED_KEYS)


def test_remembered_checks_stay_bounded(monkeypatch):
    monkeypatch.setattr(schema, "_PASSED_LIMIT", 4)
    for index in range(10):
        freeze({f"key{index}": f"value {index}"})
        assert _remembered() <= 4
    monkeypatch.setattr(schema, "_PASSED_LIMIT", 1 << 15)
    monkeypatch.setattr(schema, "_PASSED_CHARS", 100)
    for index in range(10):
        freeze("y" * 30 + str(index))
        assert schema._passed_chars <= 100
        assert sum(map(len, schema._PASSED_TEXT | schema._PASSED_KEYS)) == schema._passed_chars
    long_text = "x" * (schema._PASSED_MAX_LENGTH + 1)
    freeze(long_text)
    assert long_text not in schema._PASSED_TEXT


def _real_values():
    loaded = registry.load_registry(BOOTSTRAP_REGISTRY)
    fixture = registry.load_registry(FIXTURE / "rules" / "registry.json")
    values = [*loaded.pack_versions.values(), *fixture.pack_versions.values(),
              *list(fixture.records.values())[:20], *fixture.artifacts.values()]
    core = fixture.packs["cosmere.core"]
    era = fixture.packs["cosmere.mistborn.era-1"]
    pins = (PackagePin(core.id, core.version, core.package_hash),
            (PackagePin(era.id, era.version, era.package_hash),))
    values.append(CosmereRulesBinding(pins[0], pins[1], "scadrial", ("era-1",)))
    values.append(CosmereRulesBinding(pins[0], (), "roshar", (), ("roshar", "scadrial")))
    values.append({"z": "é", "a": [1, 2.5, None, True], "m": MappingProxyType({"k": (1,)})})
    return values


def test_to_dict_and_canonical_json_match_the_old_code_byte_for_byte():
    for value in _real_values():
        assert to_dict(value) == _old_to_dict(value)
        assert canonical_json(value) == _old_canonical_json(value)
        assert package_hash(value) == package_hash(_old_to_dict(value))


@pytest.mark.parametrize("value", GOOD + BAD)
def test_canonical_json_decides_and_serializes_like_the_old_one(value):
    assert _outcome(canonical_json, value) == _outcome(_old_canonical_json, value)


def _write(tmp_path, name, raw):
    path = tmp_path / name
    path.write_bytes(raw)
    return path


def _registry_document():
    return json.loads((FIXTURE / "rules" / "registry.json").read_bytes())


def _rehash(document):
    document["package_hash"] = package_hash(document)
    return (json.dumps(document, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def test_an_equivalent_rewrite_still_loads_through_the_slow_comparison(tmp_path):
    original = registry.load_registry(FIXTURE / "rules" / "registry.json")
    pretty = json.dumps(_registry_document(), indent=2, ensure_ascii=False).encode("utf-8")
    crlf = (FIXTURE / "rules" / "registry.json").read_bytes().replace(b"\n", b"\r\n")
    for name, raw in (("pretty.json", pretty), ("crlf.json", crlf)):
        loaded = registry.load_registry(_write(tmp_path, name, raw))
        assert loaded.package_hash == original.package_hash
        assert set(loaded.pack_versions) == set(original.pack_versions)
        assert loaded.records == original.records


def test_tampering_is_still_refused(tmp_path):
    document = _registry_document()
    document["package_hash"] = "0" * 64
    raw = json.dumps(document).encode("utf-8")
    with pytest.raises(RulesValidationError, match="registry hash mismatch"):
        registry.load_registry(_write(tmp_path, "hash.json", raw))

    document = _registry_document()
    record = next(pack for pack in document["packs"] if pack["records"])["records"][0]
    record["name"] = record["name"] + " edited"
    with pytest.raises(RulesValidationError, match="package hash mismatch"):
        registry.load_registry(_write(tmp_path, "record.json", _rehash(document)))

    document = _registry_document()
    document["packs"].reverse()
    with pytest.raises(RulesValidationError, match="compiled registry mismatch"):
        registry.load_registry(_write(tmp_path, "order.json", _rehash(document)))

    document = _registry_document()
    document["extra"] = 1
    with pytest.raises(RulesValidationError, match="invalid compiled registry fields"):
        registry.load_registry(_write(tmp_path, "extra.json", json.dumps(document).encode()))


def test_load_reuses_the_validated_packages_in_file_order(tmp_path):
    loaded = registry.load_registry(FIXTURE / "rules" / "registry.json")
    document = _registry_document()
    assert [(pack.id, pack.version) for pack in loaded.pack_versions.values()] == [
        (pack["id"], pack["version"]) for pack in document["packs"]]
    for pack in loaded.pack_versions.values():
        assert to_dict(pack) == next(
            item for item in document["packs"]
            if (item["id"], item["version"]) == (pack.id, pack.version))
    _bytes, artifacts, packs = compiler.compile_and_validate(
        {key: document[key] for key in ("schema_version", "artifacts", "packs")})
    assert _bytes == (FIXTURE / "rules" / "registry.json").read_bytes().replace(b"\r\n", b"\n")
    assert dict(artifacts) == dict(loaded.artifacts)
