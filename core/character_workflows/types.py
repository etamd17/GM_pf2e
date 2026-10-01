"""Explicit values shared by character workflow boundaries."""

from dataclasses import dataclass
from typing import Any, Literal

JSON = dict[str, Any]
DraftKind = Literal['pf2e_builder', 'pf2e_import', 'cosmere_builder', 'cosmere_import']


@dataclass(frozen=True, slots=True)
class DraftInput:
    kind: DraftKind
    payload_version: int
    form: JSON
    ui: JSON
    submission: JSON


DraftState = Literal['active', 'publishing', 'committed', 'discarded']


@dataclass(frozen=True, slots=True)
class DraftSnapshot:
    id: str
    campaign_id: str
    author_id: str
    system: str
    inputs: DraftInput | None
    state: DraftState
    revision: int
    target_id: str | None
    base_fingerprint: str | None
    created_at: str
    updated_at: str
    result: JSON | None


@dataclass(frozen=True, slots=True)
class WorkflowReceipt:
    id: str
    campaign_id: str
    author_id: str
    operation: str
    key_hash: str
    request_digest: str
    draft_id: str | None
    target_id: str | None
    state: str
    metadata: JSON


@dataclass(frozen=True, slots=True)
class PublishResult:
    character_id: str
    system: str
    created: bool
    revision: int


class WorkflowError(Exception):
    """A safe public error, with no internal path or exception disclosure."""

    def __init__(self, code: str, message: str, status: int,
                 issues: tuple[str, ...] = ()):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.issues = issues


@dataclass(frozen=True, slots=True)
class CharacterRecord:
    campaign_id: str
    character_id: str
    system: str
    storage: str
    filename: str
    document: JSON
    owner_id: str | None
    editor_ids: frozenset[str]
    viewer_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class Capabilities:
    create: bool = False
    view: bool = False
    edit: bool = False
    delete: bool = False
    manage_ownership: bool = False
    override: bool = False
    owner_private: bool = False
