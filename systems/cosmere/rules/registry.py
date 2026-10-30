"""Fail-closed compiled registry loading and exact binding resolution."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from collections.abc import Mapping

from .compiler import (COMPILER_VERSION, MAX_JSON_BYTES, canonical_json, compile_and_validate,
                       package_hash, read_json)
from .schema import (ContentPack, CosmereRulesBinding, PackagePin, RuleRecord,
                     SourceArtifact, from_dict, require, sha256)


@dataclass(frozen=True)
class RulesRegistry:
    artifacts: Mapping[str, SourceArtifact]
    packs: Mapping[str, ContentPack]
    records: Mapping[str, RuleRecord]
    package_hash: str
    pack_versions: Mapping[tuple[str, str, str], ContentPack] = None

    def __post_init__(self):
        if self.pack_versions is None:
            exact = {(pack.id, pack.version, pack.package_hash): pack
                     for pack in self.packs.values()}
            object.__setattr__(self, "pack_versions", MappingProxyType(exact))

    def resolve_pin(self, pin: PackagePin) -> ContentPack:
        pin = pin if isinstance(pin, PackagePin) else from_dict(PackagePin, pin)
        identity = (pin.id, pin.version, pin.package_hash)
        # Preserve test/homebrew callers that replace the historical ID mapping
        # on an immutable registry value while retaining its trusted pin.
        legacy = self.packs.get(pin.id)
        if (legacy is not None and legacy.version == pin.version
                and legacy.package_hash == pin.package_hash):
            return legacy
        require(identity in self.pack_versions, "pinned package version/hash mismatch")
        return self.pack_versions[identity]

    def resolve_binding(self, binding: CosmereRulesBinding) -> tuple[ContentPack, ...]:
        binding = binding if isinstance(binding, CosmereRulesBinding) else from_dict(CosmereRulesBinding, binding)
        selected = tuple(self.resolve_pin(pin) for pin in (binding.core_package, *binding.enabled_packs))
        require(selected[0].kind in {"core", "compatibility"}, "binding requires a core or compatibility package")
        enabled = {pack.id for pack in selected}
        require(len(enabled) == len(selected), "duplicate bound pack IDs")
        scoped_eras = set()
        scoped_worlds = set()
        for pack in selected:
            require(set(pack.dependencies) <= enabled, "binding missing package dependency")
            if "cosmere" not in pack.worlds:
                require(bool(set(pack.worlds) & set(binding.allowed_worlds)),
                        "binding world outside package scope")
                scoped_worlds.update(pack.worlds)
            if pack.eras:
                require(bool(set(binding.allowed_eras) & set(pack.eras)), "binding eras outside package scope")
                scoped_eras.update(pack.eras)
        require(set(binding.allowed_worlds) <= scoped_worlds,
                "binding world lacks an enabled package")
        require(not scoped_eras or set(binding.allowed_eras) <= scoped_eras, "binding era lacks an enabled package")
        return selected


def load_registry(path: Path, *, expected_hash: str | None = None) -> RulesRegistry:
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "invalid registry file")
    require(path.stat().st_size <= MAX_JSON_BYTES, "JSON size limit exceeded")
    with path.open("rb") as handle:
        raw = handle.read(MAX_JSON_BYTES + 1)
    data = read_json(raw)
    require(isinstance(data, dict) and set(data) == {"schema_version", "compiler_version", "artifacts", "packs", "package_hash"}, "invalid compiled registry fields")
    require(data["compiler_version"] == COMPILER_VERSION, "unsupported compiler version")
    sha256(data["package_hash"])
    require(package_hash(data) == data["package_hash"], "registry hash mismatch")
    if expected_hash is not None:
        sha256(expected_hash)
        require(expected_hash == data["package_hash"], "trusted registry hash mismatch")
    authoring = {key: data[key] for key in ("schema_version", "artifacts", "packs")}
    compiled, artifacts, packs = compile_and_validate(authoring)
    # An installed registry is the compiler's exact output, so comparing bytes
    # settles it; re-serializing is only needed for an equivalent rewrite.
    require(raw.replace(b"\r\n", b"\n") == compiled or canonical_json(data) == compiled,
            "compiled registry mismatch")
    # Validation built these from data["packs"], in the same order.
    compiled_packs = packs.all_versions()
    exact = {(pack.id, pack.version, pack.package_hash): pack for pack in compiled_packs}
    require(len(exact) == len(compiled_packs), "duplicate exact package identity")
    legacy = {}
    records = {}
    for pack in compiled_packs:
        legacy.setdefault(pack.id, pack)
        if legacy[pack.id] is pack:
            for record in pack.records:
                records.setdefault(record.id, record)
    return RulesRegistry(MappingProxyType(artifacts), MappingProxyType(legacy),
                         MappingProxyType(records), data["package_hash"],
                         MappingProxyType(exact))
