"""Validated metadata and private-storage import for owned adventure assets.

Repository manifests contain provenance and relative private-storage references
only. Source paths are runtime arguments and are never retained by these records.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import tempfile

from .rules.schema import (
    RuleRecord,
    RulesValidationError,
    SPOILER_RANK,
    identifier,
    integer,
    require,
    sequence,
    sha256,
    text,
)


_ASSET_TYPES = frozenset({"handout", "map", "token"})
_ENTRY_FIELDS = frozenset({
    "id",
    "asset_type",
    "source_artifact_id",
    "expected_sha256",
    "source_page",
    "storage_ref",
})
_MANIFEST_FIELDS = frozenset({"adventure_id", "entries"})
_MIME_EXTENSIONS = {
    "application/pdf": frozenset({".pdf"}),
    "image/png": frozenset({".png"}),
    "image/jpeg": frozenset({".jpg", ".jpeg"}),
    "image/webp": frozenset({".webp"}),
}
_ALLOWED_MIME = {
    "handout": frozenset(_MIME_EXTENSIONS),
    "map": frozenset(_MIME_EXTENSIONS),
    "token": frozenset({"image/png", "image/jpeg", "image/webp"}),
}
_CAMPAIGN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _storage_reference(value):
    text(value, "private storage reference")
    require("\\" not in value and ":" not in value and "\x00" not in value,
            "invalid private storage reference")
    path = PurePosixPath(value)
    require(not path.is_absolute() and path.parts
            and all(part not in ("", ".", "..") for part in path.parts),
            "invalid private storage reference")
    require(str(path) == value, "invalid private storage reference")
    require(path.suffix.lower() in set().union(*_MIME_EXTENSIONS.values()),
            "invalid private storage reference")
    return value


@dataclass(frozen=True)
class PrivateAssetEntry:
    """One logical adventure asset and its private import destination."""

    id: str
    asset_type: str
    source_artifact_id: str
    expected_sha256: str
    source_page: int
    storage_ref: str

    def __post_init__(self):
        identifier(self.id)
        require(self.asset_type in _ASSET_TYPES, "invalid adventure asset type")
        identifier(self.source_artifact_id)
        sha256(self.expected_sha256)
        integer(self.source_page, "asset source page", 1)
        _storage_reference(self.storage_ref)


@dataclass(frozen=True)
class PrivateAssetManifest:
    """Private asset metadata for one normalized adventure manifest."""

    adventure_id: str
    entries: tuple[PrivateAssetEntry, ...]

    def __post_init__(self):
        identifier(self.adventure_id)
        entries = sequence(self.entries, "private asset entries")
        normalized = []
        for entry in entries:
            require(isinstance(entry, PrivateAssetEntry),
                    "invalid private asset entry")
            normalized.append(entry)
        require(len({entry.id for entry in normalized}) == len(normalized),
                "duplicate private asset IDs")
        require(len({entry.storage_ref.casefold() for entry in normalized})
                == len(normalized),
                "duplicate private storage references")
        object.__setattr__(self, "entries", tuple(normalized))


def parse_private_asset_manifest(value):
    """Strictly parse repository JSON without accepting source paths or payloads."""
    require(isinstance(value, Mapping)
            and set(value) == _MANIFEST_FIELDS,
            "invalid private asset manifest fields")
    raw_entries = sequence(value["entries"], "private asset entries")
    entries = []
    for item in raw_entries:
        require(isinstance(item, Mapping) and set(item) == _ENTRY_FIELDS,
                "invalid asset entry fields")
        try:
            entries.append(PrivateAssetEntry(**dict(item)))
        except TypeError:
            raise RulesValidationError("invalid asset entry fields") from None
    return PrivateAssetManifest(
        adventure_id=value["adventure_id"],
        entries=tuple(entries),
    )


def visible_private_assets(manifest, *, records, include_gm=False):
    """Return assets visible to a player unless GM visibility is explicit."""
    require(isinstance(manifest, PrivateAssetManifest),
            "invalid private asset manifest")
    require(isinstance(records, Mapping), "invalid adventure record index")
    require(type(include_gm) is bool, "invalid GM visibility flag")
    result = []
    maximum = SPOILER_RANK["gm" if include_gm else "player"]
    for entry in manifest.entries:
        record = records.get(entry.id)
        require(isinstance(record, RuleRecord) and record.kind == "handout",
                "asset does not resolve to a handout record")
        if SPOILER_RANK[record.spoiler_scope] <= maximum:
            result.append(entry)
    return tuple(result)


def _detected_mime(header):
    if header.startswith(b"%PDF-"):
        return "application/pdf"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    raise RulesValidationError("unsupported asset signature")


def _campaign_root(storage_root, campaign_id):
    require(isinstance(campaign_id, str)
            and _CAMPAIGN_ID.fullmatch(campaign_id) is not None
            and campaign_id not in (".", ".."),
            "invalid campaign identity")
    root = Path(storage_root)
    root.mkdir(parents=True, exist_ok=True)
    campaigns = root / "campaigns"
    campaigns.mkdir(parents=True, exist_ok=True)
    campaign = campaigns / campaign_id
    campaign.mkdir(parents=True, exist_ok=True)
    resolved_campaigns = campaigns.resolve(strict=True)
    resolved_campaign = campaign.resolve(strict=True)
    try:
        resolved_campaign.relative_to(resolved_campaigns)
    except ValueError:
        raise RulesValidationError("invalid campaign identity") from None
    return root, resolved_campaign


def import_private_asset(entry, *, source_path, storage_root, campaign_id,
                         declared_mime, gm_authorized):
    """Copy one verified owned asset into campaign-private storage atomically.

    Authentication is performed by the caller; this boundary requires the
    resulting explicit GM authorization and otherwise fails closed.
    """
    require(isinstance(entry, PrivateAssetEntry), "invalid private asset entry")
    if gm_authorized is not True:
        raise PermissionError("GM authorization is required for private asset import")
    require(declared_mime in _MIME_EXTENSIONS, "unsupported declared MIME")
    source = Path(source_path)
    require(source.is_file(), "owned asset source is not a file")
    root, campaign = _campaign_root(storage_root, campaign_id)
    destination = campaign.joinpath(*PurePosixPath(entry.storage_ref).parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = destination.parent.resolve(strict=True)
    try:
        resolved_parent.relative_to(campaign)
    except ValueError:
        raise RulesValidationError("private storage reference escapes campaign") from None
    destination = resolved_parent / destination.name

    temporary_name = None
    digest = hashlib.sha256()
    header = b""
    try:
        with source.open("rb") as incoming, tempfile.NamedTemporaryFile(
            mode="wb", dir=resolved_parent, prefix=f".{destination.name}.",
            suffix=".tmp", delete=False
        ) as outgoing:
            temporary_name = outgoing.name
            while True:
                chunk = incoming.read(1024 * 1024)
                if not chunk:
                    break
                if len(header) < 16:
                    header += chunk[:16 - len(header)]
                digest.update(chunk)
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())

        detected_mime = _detected_mime(header)
        require(detected_mime == declared_mime,
                "declared MIME does not match asset signature")
        require(detected_mime in _ALLOWED_MIME[entry.asset_type],
                f"{detected_mime} is not allowed for {entry.asset_type} assets")
        require(PurePosixPath(entry.storage_ref).suffix.lower()
                in _MIME_EXTENSIONS[detected_mime],
                "private storage extension does not match asset MIME")
        require(digest.hexdigest() == entry.expected_sha256,
                "asset hash mismatch")
        os.replace(temporary_name, destination)
        temporary_name = None
        return root / "campaigns" / campaign_id / Path(entry.storage_ref)
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass
