"""Owner-private character fields shared by workflow adapters and UI projections."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict

from .types import JSON


OWNER_PRIVATE_BUILD_FIELDS = ("notes", "session_notes")


def draft_client_payload(draft) -> JSON:
    """Serialize a draft without its server-owned publication fingerprint.

    Legacy fingerprints can be derived from character fields that are now
    owner-private. Clients neither need nor control this value, so keep it on
    the trusted persistence/publication boundary.
    """

    result = asdict(draft)
    result.pop("base_fingerprint", None)
    return result


def preserve_owner_private_build_fields(current: JSON, updated: JSON) -> JSON:
    """Return ``updated`` with private build fields taken only from ``current``."""

    result = deepcopy(updated)
    current_build = current.get("build") if isinstance(current, dict) else None
    result_build = result.get("build") if isinstance(result, dict) else None
    if not isinstance(result_build, dict):
        return result

    for field in OWNER_PRIVATE_BUILD_FIELDS:
        if isinstance(current_build, dict) and field in current_build:
            result_build[field] = deepcopy(current_build[field])
        else:
            result_build.pop(field, None)
    return result


def without_owner_private_build_fields(document: JSON) -> JSON:
    """Return a copy safe to cross an editor-facing presentation boundary."""

    result = deepcopy(document)
    build = result.get("build") if isinstance(result, dict) else None
    if isinstance(build, dict):
        for field in OWNER_PRIVATE_BUILD_FIELDS:
            build.pop(field, None)
    return result
