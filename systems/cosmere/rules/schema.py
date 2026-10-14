"""Cosmere-specific immutable records and validation at every construction boundary."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
import math
import re
from types import MappingProxyType


class RulesValidationError(ValueError):
    """Invalid authoring data, binding, source metadata, or compiled package."""


ID_PATTERN = r"[a-z][a-z0-9]*(?:[-][a-z0-9]+)*(?:\.[a-z][a-z0-9]*(?:[-][a-z0-9]+)*)+"
SEMVER_PATTERN = (r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
                  r"(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
                  r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?"
                  r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?")
WORLDS = frozenset({"cosmere", "roshar", "scadrial"})
ERAS = frozenset({"era-1", "era-2"})
SPOILERS = frozenset({"none", "player", "gm"})
SPOILER_RANK = MappingProxyType({"none": 0, "player": 1, "gm": 2})
AUDIT_STATUSES = frozenset({"provisional", "verified", "blocked"})
AUTOMATION = frozenset({"calculated", "tracked", "reference", "gm_adjudicated"})
AUTHORITY_TIERS = ("errata", "handbook", "world-guide", "adventure", "gm-summary", "quickstart")
FORBIDDEN_KEYS = frozenset({
    "raw_text", "extracted_text", "extracted_prose", "extracted_pages", "source_text",
    "pdf_text", "raw_pdf", "pdf_bytes", "pdf_binary", "raw_source", "source_binary",
    "source_binaries", "source_bytes", "source_media", "page_image", "page_images",
    "page_image_data", "image_data", "media_data", "media_payload", "audio_data",
    "video_data", "binary_data", "binary_payload", "blob", "watermark", "watermarks",
    "customer_watermark", "local_path", "absolute_path",
})
MEDIA_PAYLOAD = re.compile(r"(?i:\bdata:[^\s,]*,)|%PDF-[0-9]|(?<![A-Za-z0-9+/])(?:iVBORw0KGgo|JVBERi0|R0lGOD|/9j/)")


def require(condition, message):
    if not condition:
        raise RulesValidationError(message)


def text(value, label, pattern=None):
    require(isinstance(value, str) and bool(value.strip()), f"invalid {label}")
    if pattern is not None:
        require(re.fullmatch(pattern, value) is not None, f"invalid {label}")
    return value


def identifier(value):
    return text(value, "namespaced ID", ID_PATTERN)


def entity_identifier(value, label="entity ID"):
    """Accept package-style IDs and opaque runtime UUID identities.

    Rules and package records remain namespaced. Runtime actors/events already
    use 32-character UUID hex values throughout the host application, so audit
    records must not reject their authoritative identities.
    """
    return text(value, label,
                rf"(?:{ID_PATTERN}|[0-9a-f]{{32}}|[0-9a-f]{{8}}-[0-9a-f]{{4}}-[1-5][0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}})")


def version(value):
    return text(value, "semantic version", SEMVER_PATTERN)


def sha256(value):
    return text(value, "SHA-256", r"[0-9a-f]{64}")


def integer(value, label, minimum=0):
    require(type(value) is int and value >= minimum, f"invalid {label}")


def choice(value, choices, label):
    require(isinstance(value, str) and value in choices, f"invalid {label}")


def sequence(value, label):
    require(isinstance(value, (list, tuple)), f"invalid {label}")
    return tuple(value)


def ids(value, label, nonempty=False):
    result = sequence(value, label)
    require(not nonempty or bool(result), f"missing {label}")
    for item in result:
        identifier(item)
    require(len(set(result)) == len(result), f"duplicate {label}")
    return result


def scopes(worlds, eras):
    worlds, eras = sequence(worlds, "worlds"), sequence(eras, "eras")
    require(bool(worlds), "missing world scope")
    for value in worlds:
        choice(value, WORLDS, "world scope")
    for value in eras:
        choice(value, ERAS, "era scope")
    require(len(set(worlds)) == len(worlds) and len(set(eras)) == len(eras), "duplicate scope")
    require(not eras or "scadrial" in worlds or "cosmere" in worlds, "era requires Scadrial scope")
    return worlds, eras


def freeze(value, depth=0):
    """Copy normalized JSON into immutable containers; reject private runtime paths."""
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
            freeze(key, depth + 1)
            normalized_key = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
            normalized_key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", normalized_key)
            normalized_key = re.sub(r"[-_\s]+", "_", normalized_key).strip("_").lower()
            require(normalized_key not in FORBIDDEN_KEYS, "raw source content is prohibited")
            result[key] = freeze(item, depth + 1)
        return MappingProxyType(result)
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item, depth + 1) for item in value)
    raise RulesValidationError("invalid normalized JSON value")


def to_dict(value):
    """Return detached JSON-compatible data without mutating frozen records."""
    if isinstance(value, CosmereRulesBinding):
        result = {field.name: to_dict(getattr(value, field.name)) for field in fields(value)}
        # A single-world binding predates ``allowed_worlds`` and is completely
        # described by its creation default.  Keep that wire representation
        # stable while mixed-world bindings serialize their explicit scope.
        if value.allowed_worlds == (value.default_world,):
            result.pop("allowed_worlds", None)
        return result
    if is_dataclass(value):
        return {field.name: to_dict(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: to_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_dict(item) for item in value]
    return value


def from_dict(cls, value):
    require(isinstance(value, Mapping), f"invalid {cls.__name__}")
    try:
        return cls(**value)
    except TypeError:
        raise RulesValidationError(f"invalid fields in {cls.__name__}") from None


def records(value, cls):
    return tuple(item if isinstance(item, cls) else from_dict(cls, item)
                 for item in sequence(value, cls.__name__))


@dataclass(frozen=True)
class SourceArtifact:
    product_id: str
    basename: str
    edition: str
    sha256: str
    byte_count: int
    page_count: int
    authority_tier: str
    extraction_class: str
    spoiler_scope: str
    rights_state: str

    def __post_init__(self):
        identifier(self.product_id)
        text(self.basename, "basename")
        require(not any(char in self.basename for char in ("/", "\\", ":")), "basename must not contain a path")
        require(self.basename.lower().endswith(".pdf"), "source artifact must be a PDF basename")
        text(self.edition, "edition")
        sha256(self.sha256)
        integer(self.byte_count, "byte count", 1)
        integer(self.page_count, "page count", 1)
        choice(self.authority_tier, AUTHORITY_TIERS, "authority tier")
        choice(self.extraction_class, {"rules", "lore", "adventure", "gm-tools", "handouts", "maps", "maps-tokens"}, "extraction class")
        choice(self.spoiler_scope, SPOILERS, "spoiler scope")
        choice(self.rights_state, {"private", "quarantined"}, "rights state")


@dataclass(frozen=True)
class SourceRef:
    artifact_id: str
    page_start: int
    page_end: int

    def __post_init__(self):
        identifier(self.artifact_id)
        integer(self.page_start, "page start", 1)
        integer(self.page_end, "page end", 1)
        require(self.page_end >= self.page_start, "invalid page range")


@dataclass(frozen=True)
class RuleRecord:
    id: str
    version: str
    kind: str
    name: str
    summary: str
    citations: tuple[SourceRef, ...]
    worlds: tuple[str, ...]
    eras: tuple[str, ...]
    spoiler_scope: str
    automation: str
    audit_status: str
    parameters: Mapping
    overrides: str | None = None
    prerequisites: tuple[str, ...] = ()
    references: tuple[str, ...] = ()

    def __post_init__(self):
        identifier(self.id)
        version(self.version)
        choice(self.kind, {"rule", "skill", "path", "talent", "resource", "action", "effect", "adversary", "item", "encounter", "scene", "handout", "grant", "origin", "power", "table", "reward"}, "rule kind")
        text(self.name, "rule name")
        text(self.summary, "original summary")
        freeze(self.name)
        freeze(self.summary)
        citations = records(self.citations, SourceRef)
        require(bool(citations), "missing citations")
        require(len(set(citations)) == len(citations), "duplicate citations")
        object.__setattr__(self, "citations", citations)
        worlds, eras = scopes(self.worlds, self.eras)
        object.__setattr__(self, "worlds", worlds)
        object.__setattr__(self, "eras", eras)
        choice(self.spoiler_scope, SPOILERS, "spoiler scope")
        choice(self.automation, AUTOMATION, "automation status")
        choice(self.audit_status, AUDIT_STATUSES, "audit status")
        require(isinstance(self.parameters, Mapping), "invalid rule parameters")
        object.__setattr__(self, "parameters", freeze(self.parameters))
        if self.overrides is not None:
            identifier(self.overrides)
        prerequisites = sequence(self.prerequisites, "rule prerequisites")
        for prerequisite in prerequisites:
            text(prerequisite, "rule prerequisite")
            freeze(prerequisite)
        require(len(set(prerequisites)) == len(prerequisites),
                "duplicate rule prerequisites")
        object.__setattr__(self, "prerequisites", prerequisites)
        references = sequence(self.references, "rule references")
        for reference in references:
            text(reference, "rule reference",
                 rf"(?:{ID_PATTERN}|{ID_PATTERN}\.\*)")
        require(len(set(references)) == len(references), "duplicate rule references")
        object.__setattr__(self, "references", references)


@dataclass(frozen=True)
class ContentPack:
    id: str
    version: str
    kind: str
    dependencies: tuple[str, ...]
    sources: tuple[str, ...]
    worlds: tuple[str, ...]
    eras: tuple[str, ...]
    spoiler_scope: str
    audit_status: str
    records: tuple[RuleRecord, ...]
    package_hash: str | None = None

    def __post_init__(self):
        identifier(self.id)
        version(self.version)
        choice(self.kind, {"core", "compatibility", "world", "adventure"}, "pack kind")
        object.__setattr__(self, "dependencies", ids(self.dependencies, "dependencies"))
        require(self.id not in self.dependencies, "pack cannot depend on itself")
        # An empty compatibility pack can bootstrap the legacy Stormlight
        # adapter without pretending to cite a source document. Records still
        # require provenance and citations.
        object.__setattr__(self, "sources", ids(self.sources, "provenance", bool(self.records)))
        worlds, eras = scopes(self.worlds, self.eras)
        object.__setattr__(self, "worlds", worlds)
        object.__setattr__(self, "eras", eras)
        choice(self.spoiler_scope, SPOILERS, "spoiler scope")
        choice(self.audit_status, AUDIT_STATUSES, "audit status")
        object.__setattr__(self, "records", records(self.records, RuleRecord))
        require(len({r.id for r in self.records}) == len(self.records), "duplicate record IDs")
        if self.kind == "adventure":
            require(all(r.overrides is None for r in self.records), "adventure may not override core rules")
        if self.package_hash is not None:
            sha256(self.package_hash)


@dataclass(frozen=True)
class PackagePin:
    id: str
    version: str
    package_hash: str

    def __post_init__(self):
        identifier(self.id)
        version(self.version)
        sha256(self.package_hash)


@dataclass(frozen=True)
class RulesScope:
    """The immutable mechanical world/era attached to one actor or encounter."""

    world: str
    era: str | None = None

    def __post_init__(self):
        choice(self.world, {"roshar", "scadrial"}, "rules scope world")
        if self.world == "scadrial":
            choice(self.era, ERAS, "rules scope era")
        else:
            require(self.era is None, "only a Scadrial rules scope may select an era")


@dataclass(frozen=True)
class CosmereRulesBinding:
    core_package: PackagePin
    enabled_packs: tuple[PackagePin, ...]
    default_world: str
    allowed_eras: tuple[str, ...]
    allowed_worlds: tuple[str, ...] = ()

    def __post_init__(self):
        core = self.core_package if isinstance(self.core_package, PackagePin) else from_dict(PackagePin, self.core_package)
        object.__setattr__(self, "core_package", core)
        packs = records(self.enabled_packs, PackagePin)
        require(len({p.id for p in packs}) == len(packs) and all(p.id != core.id for p in packs), "duplicate bound pack IDs")
        object.__setattr__(self, "enabled_packs", packs)
        choice(self.default_world, {"roshar", "scadrial"}, "default world")
        worlds = sequence(self.allowed_worlds, "allowed worlds")
        if not worlds:
            worlds = (self.default_world,)
        for world in worlds:
            choice(world, {"roshar", "scadrial"}, "allowed world")
        require(len(set(worlds)) == len(worlds), "duplicate allowed worlds")
        require(self.default_world in worlds, "default world must be allowed")
        eras = sequence(self.allowed_eras, "allowed eras")
        for era in eras:
            choice(era, ERAS, "allowed era")
        require(len(set(eras)) == len(eras), "duplicate allowed eras")
        require(not eras or "scadrial" in worlds, "era requires an allowed Scadrial world")
        object.__setattr__(self, "allowed_worlds", tuple(worlds))
        object.__setattr__(self, "allowed_eras", eras)


@dataclass(frozen=True)
class AdvancementEvent:
    id: str
    actor_id: str
    action: str
    prior_revision: int
    revision: int
    from_level: int
    to_level: int
    package_hash: str
    choices: Mapping
    consumed_goal_ids: tuple[str, ...]
    prior_snapshot: Mapping = field(default_factory=dict)
    resulting_snapshot: Mapping = field(default_factory=dict)
    audit_metadata: Mapping = field(default_factory=dict)
    reverts_event_id: str | None = None
    package_pins: tuple[PackagePin, ...] = ()

    def __post_init__(self):
        entity_identifier(self.id, "advancement event ID")
        entity_identifier(self.actor_id, "actor ID")
        choice(self.action, {"advance", "revert"}, "advancement action")
        integer(self.prior_revision, "prior revision")
        integer(self.revision, "revision", 1)
        require(self.revision == self.prior_revision + 1, "revision must increase by one")
        integer(self.from_level, "from level", 1)
        integer(self.to_level, "to level", 1)
        require(self.to_level == self.from_level + (1 if self.action == "advance" else -1), "event must change exactly one level")
        sha256(self.package_hash)
        require(isinstance(self.choices, Mapping), "invalid advancement choices")
        object.__setattr__(self, "choices", freeze(self.choices))
        object.__setattr__(self, "consumed_goal_ids", ids(self.consumed_goal_ids, "consumed goals"))
        for name in ("prior_snapshot", "resulting_snapshot", "audit_metadata"):
            value = getattr(self, name)
            require(isinstance(value, Mapping), f"invalid {name.replace('_', ' ')}")
            object.__setattr__(self, name, freeze(value))
        if self.reverts_event_id is not None:
            entity_identifier(self.reverts_event_id, "reverted event ID")
            require(self.action == "revert", "only revert events identify a reverted event")
        pins = records(self.package_pins, PackagePin)
        require(len({pin.id for pin in pins}) == len(pins),
                "duplicate advancement package pins")
        object.__setattr__(self, "package_pins", pins)


@dataclass(frozen=True)
class EffectInstance:
    id: str
    source_id: str
    target_id: str
    parameters: Mapping
    selected_attribute: str | None
    stacking_identity: str
    duration_unit: str
    remaining_duration: int | None
    trigger: str
    refresh_policy: str
    expiry_state: Mapping = field(default_factory=dict)
    expired: bool = False

    def __post_init__(self):
        entity_identifier(self.id, "effect instance ID")
        entity_identifier(self.source_id, "effect source ID")
        entity_identifier(self.target_id, "effect target ID")
        identifier(self.stacking_identity)
        require(isinstance(self.parameters, Mapping), "invalid effect parameters")
        object.__setattr__(self, "parameters", freeze(self.parameters))
        if self.selected_attribute is not None:
            choice(self.selected_attribute, {"strength", "speed", "intellect", "willpower", "awareness", "presence"}, "selected attribute")
        choice(self.duration_unit, {"round", "turn", "contribution", "minute", "hour", "scene", "encounter", "permanent"}, "duration unit")
        if self.duration_unit == "permanent":
            require(self.remaining_duration is None, "permanent effect has no remaining duration")
        else:
            integer(self.remaining_duration, "remaining duration")
        choice(self.trigger, {"turn_start", "turn_end", "phase_start", "round_end",
                              "contribution_end", "scene_end", "encounter_end", "manual"},
               "effect trigger")
        choice(self.refresh_policy, {"refresh", "independent", "replace", "extend"}, "refresh policy")
        require(isinstance(self.expiry_state, Mapping), "invalid expiry state")
        object.__setattr__(self, "expiry_state", freeze(self.expiry_state))
        require(type(self.expired) is bool, "invalid expired state")


@dataclass(frozen=True)
class AdventureManifest:
    id: str
    version: str
    pack_id: str
    sources: tuple[str, ...]
    worlds: tuple[str, ...]
    eras: tuple[str, ...]
    spoiler_scope: str
    audit_status: str
    records: tuple[RuleRecord, ...]

    def __post_init__(self):
        identifier(self.id)
        identifier(self.pack_id)
        pack = ContentPack(self.pack_id, self.version, "adventure", (), self.sources,
                           self.worlds, self.eras, self.spoiler_scope, self.audit_status, self.records)
        for field in ("sources", "worlds", "eras", "records"):
            object.__setattr__(self, field, getattr(pack, field))
