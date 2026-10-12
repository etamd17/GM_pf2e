"""Validate the same local JSON schemas used by the generated browser validators."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry, Resource

MAX_SAFE_INTEGER = 9_007_199_254_740_991
MAX_JSON_BYTES = 2_097_152
MAX_JSON_DEPTH = 64
SCHEMA_KEYWORDS = {
    "$schema", "$id", "$ref", "title", "description", "type", "const", "enum",
    "properties", "required", "additionalProperties", "definitions", "oneOf", "anyOf",
    "items", "minLength", "maxLength", "pattern", "minimum", "maximum", "minItems",
    "maxItems", "uniqueItems",
}


class ProtocolValidationError(ValueError):
    """A detail-free boundary failure, suitable for a sanitized protocol adapter."""

    code = "invalid-schema"


def _reject_constant(_value):
    raise ProtocolValidationError("Non-finite JSON is forbidden")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolValidationError("Duplicate JSON keys are forbidden")
        result[key] = value
    return result


def _check_json(value, depth=0):
    if depth > MAX_JSON_DEPTH:
        raise ProtocolValidationError("JSON nesting limit exceeded")
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ProtocolValidationError("Non-finite JSON is forbidden")
        if (isinstance(value, int) or value.is_integer()) and abs(value) > MAX_SAFE_INTEGER:
            raise ProtocolValidationError("Unsafe JSON integer")
        return
    if isinstance(value, list):
        for item in value:
            _check_json(item, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProtocolValidationError("JSON object keys must be strings")
            _check_json(item, depth + 1)
        return
    raise ProtocolValidationError("Only JSON values are accepted")


class StrictJSON:
    """Pass this module-like object as Socket.IO's json= argument when wiring VTT.

    REST adapters must also use loads(), rather than a permissive request JSON parser.
    No server is wired in this schema batch, so no endpoint yet claims these guarantees.
    """

    @staticmethod
    def loads(payload, **kwargs):
        hooks = {"parse_constant", "parse_int", "parse_float", "object_pairs_hook", "object_hook", "cls"}
        if hooks.intersection(kwargs) or kwargs.get("strict") is False:
            raise ProtocolValidationError("Strict JSON hooks cannot be overridden")
        try:
            if isinstance(payload, (bytes, bytearray)):
                payload = payload.decode("utf-8", errors="strict")
            if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_JSON_BYTES:
                raise ProtocolValidationError("JSON size limit exceeded")
            value = json.loads(payload, parse_constant=_reject_constant, object_pairs_hook=_unique_object, **kwargs)
            _check_json(value)
            return value
        except (UnicodeError, json.JSONDecodeError, RecursionError, TypeError) as error:
            raise ProtocolValidationError("Invalid JSON") from error

    @staticmethod
    def dumps(value, **kwargs):
        if kwargs.get("allow_nan") or "default" in kwargs or "cls" in kwargs:
            raise ProtocolValidationError("Strict JSON serialization cannot be overridden")
        _check_json(value)
        kwargs["allow_nan"] = False
        try:
            result = json.dumps(value, **kwargs)
            if len(result.encode("utf-8")) > MAX_JSON_BYTES:
                raise ProtocolValidationError("JSON size limit exceeded")
            return result
        except (UnicodeError, ValueError, RecursionError, TypeError) as error:
            raise ProtocolValidationError("Invalid JSON") from error


def _deny_remote(_uri):
    raise ProtocolValidationError("Schema retrieval is disabled")


def _check_refs(value, source: Path, files: set[Path]):
    if isinstance(value, dict):
        if set(value).difference(SCHEMA_KEYWORDS):
            raise ProtocolValidationError("Unsupported canonical schema keyword")
        if "$ref" in value:
            reference = urlsplit(value["$ref"])
            if reference.scheme or reference.netloc or reference.query or "\\" in reference.path:
                raise ProtocolValidationError("Only local schema references are accepted")
            target = (source.parent / reference.path).resolve() if reference.path else source
            if target not in files:
                raise ProtocolValidationError("Missing local schema reference")
        if value.get("type") == "integer" and not {"minimum", "maximum"}.issubset(value):
            raise ProtocolValidationError("Every integer schema must be explicitly bounded")
        for keyword in ("properties", "definitions"):
            for child in value.get(keyword, {}).values():
                _check_refs(child, source, files)
        for keyword in ("oneOf", "anyOf"):
            for child in value.get(keyword, []):
                _check_refs(child, source, files)
        for keyword in ("items", "additionalProperties"):
            if isinstance(value.get(keyword), dict):
                _check_refs(value[keyword], source, files)


class ContractRegistry:
    """Preload immutable validators; no network, CWD assumptions, or permissive fallback."""

    def __init__(self, root: Path | None = None):
        self.root = (root or Path(__file__).resolve().parents[2] / "contracts" / "vtt" / "v1").resolve()
        self.manifest = StrictJSON.loads((self.root / "registry.json").read_text(encoding="utf-8"))
        if type(self.manifest.get("schemaVersion")) is not int or self.manifest["schemaVersion"] != 1:
            raise ProtocolValidationError("Unsupported contract registry version")
        paths = sorted((self.root / "schemas").glob("*.schema.json"))
        files = {path.resolve() for path in paths}
        schemas = {}
        resources = []
        ids = set()
        for path in paths:
            value = StrictJSON.loads(path.read_text(encoding="utf-8"))
            _check_refs(value, path.resolve(), files)
            try:
                Draft7Validator.check_schema(value)
            except SchemaError as error:
                raise ProtocolValidationError("Invalid canonical schema") from error
            if value.get("$schema") != "http://json-schema.org/draft-07/schema#" or value.get("$id") in ids:
                raise ProtocolValidationError("Invalid schema dialect or duplicate identifier")
            ids.add(value["$id"])
            schemas[path.resolve()] = value
            resources.append((value["$id"], Resource.from_contents(value)))
        local = Registry(retrieve=_deny_remote).with_resources(resources)
        self.validators = {}
        for key, name in self.manifest["schemas"].items():
            target = (self.root / name).resolve()
            if target not in schemas:
                raise ProtocolValidationError("Missing registered schema")
            self.validators[key] = Draft7Validator(schemas[target], registry=local)

    def validate(self, schema_key: str, message):
        _check_json(message)
        validator = self.validators.get(schema_key)
        if validator is None:
            raise ProtocolValidationError("Unknown schema")
        try:
            validator.validate(message)
        except ValidationError as error:
            # Error paths can disclose private fields; never serialize the library error.
            raise ProtocolValidationError("Message failed the canonical schema") from error

    def parse_and_validate(self, schema_key: str, payload):
        message = StrictJSON.loads(payload)
        self.validate(schema_key, message)
        return message

    def schema_for_event(self, event: str, audience: str) -> str:
        entry = self.manifest["events"].get(event, {})
        key = entry.get("schemas", {}).get(audience)
        if entry.get("status") != "schema-ready" or key not in self.validators:
            raise ProtocolValidationError("Event or audience not implemented")
        return key

    def source_digest(self) -> str:
        digest = hashlib.sha256()
        paths = [self.root / "registry.json", *sorted((self.root / "schemas").glob("*.schema.json"))]
        for path in sorted(paths):
            relative = path.relative_to(self.root).as_posix().encode("utf-8")
            normalized = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            digest.update(relative + b"\0" + normalized + b"\0")
        return digest.hexdigest()
