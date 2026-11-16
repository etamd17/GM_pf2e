"""Checks 4-9 of the command pipeline (contract section 2; PE-13b).

PE-14b's executor runs check 1 (``check_envelope``, PE-14a) before its
transaction and checks 2-3 (authority fence, receipt lookup) under the stream
lock.  It then builds a :class:`CheckContext`, calls the command type's
``lock_plan(ctx)`` to lock every row the checks will read, and calls
:func:`run_checks`.  The runner answers the first failing check with a
:class:`CommandRefusal` carrying the contract's code (C:352-366):

4. role, then override permission (``403 not_permitted``);
5. the subject, every payload id and every ``expected`` reference are locked,
   exist, are visible and relate to the subject as declared
   (``404 not_found``, one identical answer for all of them), then the
   caller's role on the subject (``403 not_permitted``).  No revision is
   compared before this passes;
6. terminal (``409 already_finalized``);
7. ``expected`` entries (``409 stale_revision``, every mismatch, projected);
8. pins: ``409 rules_mismatch``, ``503 content_mismatch`` (rule-deriving
   types only), ``422 edition_mismatch``;
9. the type's rules adapter.

Rows are read only through ``ctx.records`` (PE-13a's ``CommandLocks``).  A
key ``lock_plan`` did not register is a caller bug (:class:`CheckOrderError`,
answered as 500); a key registered as ``None`` means "no such key in this
campaign" and answers 404.  Records come only from a ``CommandLocks`` bound
to ``ctx.campaign_id``, whose reads all filter on it, so "same campaign"
holds by construction; a ``campaign`` record must also carry that id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Protocol

from core.persistence.state_repositories import KIND_BUILD, StateRecord

# The code table (C:352-366): every code a command can answer, with its status.
_CODES: dict[str, int] = {
    "bad_envelope": 400,
    "not_permitted": 403,
    "not_found": 404,
    **dict.fromkeys(
        ("operation_id_reused", "already_finalized", "stale_revision", "rules_mismatch",
         "item_locked", "insufficient_funds", "insufficient_stock",
         "insufficient_quantity", "abandoned", "resync_required"), 409),
    **dict.fromkeys(
        ("validation_failed", "rule_violation", "not_live", "in_encounter",
         "unlock_required", "edition_mismatch", "target_invalid"), 422),
    "server_error": 500,
    **dict.fromkeys(
        ("migration_in_progress", "authority_unavailable", "content_mismatch",
         "temporarily_unavailable"), 503),
}
CODES: Mapping[str, int] = MappingProxyType(_CODES)
CHARACTER_KINDS = frozenset({KIND_BUILD, "character.play"})


class CommandRefusal(Exception):
    """A refusal with a contract code.  PE-14b decides whether it writes a receipt."""

    def __init__(self, code: str, *, conflicts: list[dict[str, Any]] | None = None,
                 current: Mapping[str, Any] | None = None) -> None:
        if code not in _CODES:
            raise ValueError(f"unknown refusal code {code!r}")
        super().__init__(code)
        self.code = code
        self.http_status = _CODES[code]
        self.conflicts = conflicts
        self.current = current


class CheckOrderError(RuntimeError):
    """The caller broke a precondition of the runner (answered as 500)."""


class Authorizer(Protocol):
    """Role and visibility answers for one principal (PE-14c implements it)."""

    def campaign_role_allows(self, type: str) -> bool: ...
    def override_allowed(self, type: str) -> bool: ...
    def visible(self, record: StateRecord) -> bool: ...
    def subject_role_allows(self, type: str, subject: StateRecord) -> bool: ...


class RulesAdapter(Protocol):
    """A command type's rules: edition match (check 8) and check 9."""

    def build_edition_matches(self, subject_record: StateRecord, pin: Mapping[str, Any]) -> bool: ...
    def check(self, ctx: "CheckContext", override: Mapping[str, Any] | None) -> None: ...


# ``relation(ctx, subject, reference) -> bool`` for one ``expected`` kind.
Relation = Callable[["CheckContext", StateRecord, StateRecord], bool]


@dataclass(frozen=True)
class CheckSpec:
    """The facts about one command type that checks 4-9 need (PE-14a's ``CommandType``).

    ``subject`` is ``(field, record kind)``: the envelope's ``subject[field]``
    is the subject id, looked up as ``(record kind, id)``.  ``payload_ids``
    maps each payload field that holds a record id (a string, or a list of
    strings) to the record kind it names.  ``dependencies`` maps each
    ``expected`` kind the type may list to its relation to the subject.
    """

    type: str
    subject: tuple[str, str]
    dependencies: Mapping[str, Relation] = field(default_factory=dict)
    payload_ids: Mapping[str, str] = field(default_factory=dict)
    terminal: Callable[["CheckContext", StateRecord], bool] = lambda ctx, subject: False
    derives_rules: bool = False


@dataclass(frozen=True)
class ExpectedKind:
    """How one ``expected`` entry kind is compared at check 7.

    ``field`` is the entry key holding the expected value (``rev`` or
    ``status``); ``current(record)`` reads the value the row holds now.  A
    kind with ``fixed`` set compares the row with that value whatever the
    entry says (a prompt is always expected ``open``, C:142).
    """

    name: str
    field: str
    current: Callable[[StateRecord], Any]
    fixed: Any = None


def _revision(record: StateRecord) -> Any:
    return record.revision


def _status(record: StateRecord) -> Any:
    return record.payload.get("status")


DEFAULT_KINDS: Mapping[str, ExpectedKind] = MappingProxyType({
    **{name: ExpectedKind(name, "rev", _revision)
       for name in (KIND_BUILD, "character.play", "encounter", "clock")},
    "prompt": ExpectedKind("prompt", "status", _status, fixed="open"),
})
_KINDS: dict[str, ExpectedKind] = dict(DEFAULT_KINDS)


def register_expected_kind(kind: ExpectedKind) -> None:
    """Add an ``expected`` kind (PE-30, PE-40, PE-42..48; import time)."""

    if kind.name in _KINDS:
        raise ValueError(f"expected kind {kind.name} is already registered")
    _KINDS[kind.name] = kind


def expected_kinds() -> Mapping[str, ExpectedKind]:
    return MappingProxyType(_KINDS)


@dataclass(eq=False)
class CheckContext:
    """Everything checks 4-9, ``lock_plan`` and ``apply`` read (PE-14b builds it once)."""

    session: Any
    locks: Any
    principal: Any
    campaign_id: str
    envelope: Mapping[str, Any]
    authorizer: Authorizer
    project: Callable[[Any, Mapping[tuple[str, str], StateRecord]], Mapping[str, Any]]
    content_descriptor: Any
    pin: Mapping[str, Any]
    emit: Callable[..., Any] | None = None
    session_id: str | None = None
    envelope_checked: bool = False
    fence_passed: bool = False
    receipt_absent: bool = False
    bind_started_session: Callable[..., Any] | None = None

    def __post_init__(self) -> None:
        if self.locks.campaign_id != self.campaign_id:
            raise CheckOrderError("locks are bound to another campaign")
        if self.locks.session is not self.session:
            raise CheckOrderError("locks are bound to another session")

    @property
    def records(self) -> Mapping[tuple[str, str], StateRecord | None]:
        return self.locks.records


_MISSING = object()


class CheckRunner:
    """Runs checks 4-9 for one command type, in the contract's fixed order."""

    def __init__(self, spec: CheckSpec, adapter: RulesAdapter,
                 kinds: Mapping[str, ExpectedKind] | None = None) -> None:
        if adapter is None:
            raise ValueError("a rules adapter is required")
        for name in ("check", "build_edition_matches"):
            if not callable(getattr(adapter, name, None)):
                raise ValueError(f"the rules adapter has no {name}()")
        self.spec = spec
        self.adapter = adapter
        self.kinds = _KINDS if kinds is None else kinds

    def run(self, ctx: CheckContext) -> None:
        for flag in ("envelope_checked", "fence_passed", "receipt_absent"):
            if getattr(ctx, flag) is not True:
                raise CheckOrderError(f"{flag} is not set")
        spec, envelope = self.spec, ctx.envelope
        override = envelope.get("override")

        # 4. Role, then override permission.
        if not ctx.authorizer.campaign_role_allows(spec.type):
            raise CommandRefusal("not_permitted")
        if override is not None and not ctx.authorizer.override_allowed(spec.type):
            raise CommandRefusal("not_permitted")

        # 5. Visibility, then the subject role.
        subject, expected = self._visible(ctx)
        if not ctx.authorizer.subject_role_allows(spec.type, subject):
            raise CommandRefusal("not_permitted")

        # 6. Terminal.
        if spec.terminal(ctx, subject):
            raise CommandRefusal("already_finalized")

        # 7. Expected entries.
        self._expected(ctx, expected)

        # 8. Pins.
        pin = ctx.pin
        if not isinstance(pin, Mapping):
            raise CheckOrderError("ctx.pin is not the campaign pin")
        rules = envelope.get("rules")
        if (not isinstance(rules, Mapping) or rules.get("profile") != pin.get("profile")
                or ("content" in rules and rules["content"] != pin.get("content"))):
            raise CommandRefusal("rules_mismatch")
        if spec.derives_rules and ctx.content_descriptor != pin.get("content"):
            raise CommandRefusal("content_mismatch")
        if subject.kind in CHARACTER_KINDS and not self.adapter.build_edition_matches(subject, pin):
            raise CommandRefusal("edition_mismatch")

        # 9. Rules and grants.
        self.adapter.check(ctx, override)

    def _visible(self, ctx: CheckContext) -> tuple[StateRecord, list[tuple[Mapping, StateRecord]]]:
        """Check 5's first half.  Returns the subject and each ``expected`` entry's record."""

        spec, envelope = self.spec, ctx.envelope
        subject_field, subject_kind = spec.subject
        subject_ref = envelope.get("subject")
        if not isinstance(subject_ref, Mapping) or not isinstance(subject_ref.get(subject_field), str):
            raise CheckOrderError(f"the envelope has no subject {subject_field}")
        keys = [(subject_kind, subject_ref[subject_field])]
        payload = envelope.get("payload") or {}
        for name, kind in spec.payload_ids.items():
            value = payload.get(name)
            if value is None:
                continue  # an optional id field left out
            ids = [value] if isinstance(value, str) else value
            if not isinstance(ids, (list, tuple)) or not all(isinstance(i, str) for i in ids):
                raise CheckOrderError(f"payload field {name} is not a record id")
            keys.extend((kind, i) for i in ids)
        entries = list(envelope.get("expected") or ())
        for entry in entries:
            kind = entry.get("kind")
            if kind not in self.kinds or kind not in spec.dependencies:
                raise CheckOrderError(f"expected kind {kind!r} passed check 1")
            if not isinstance(entry.get("id"), str):
                raise CheckOrderError("an expected entry has no id")
            keys.append((kind, entry["id"]))

        # Every key must have been registered by lock_plan before any answer.
        records = ctx.records
        found = [records.get(key, _MISSING) for key in keys]
        for key, record in zip(keys, found):
            if record is _MISSING:
                raise CheckOrderError(f"{key[0]}:{key[1]} was not locked by lock_plan")
            if record is not None and (record.kind, record.id) != key:
                raise CheckOrderError(f"{key[0]}:{key[1]} holds another record")
        for key, record in zip(keys, found):
            if (record is None
                    or (record.kind == "campaign" and record.id != ctx.campaign_id)
                    or not ctx.authorizer.visible(record)):
                raise CommandRefusal("not_found")
        subject = found[0]
        pairs = list(zip(entries, found[len(found) - len(entries):]))
        for entry, record in pairs:
            if not spec.dependencies[entry["kind"]](ctx, subject, record):
                raise CommandRefusal("not_found")
        return subject, pairs

    def _expected(self, ctx: CheckContext, pairs: Iterable[tuple[Mapping, StateRecord]]) -> None:
        conflicts, rows = [], {}
        for entry, record in pairs:
            kind = self.kinds[entry["kind"]]
            expected = kind.fixed if kind.fixed is not None else entry.get(kind.field)
            current = kind.current(record)
            if expected != current:
                conflicts.append({"kind": kind.name, "id": record.id,
                                  "expected": expected, "current": current})
                rows[(kind.name, record.id)] = record
        if conflicts:
            current = dict(ctx.project(ctx.principal, MappingProxyType(rows)))
            shown = [c for c in conflicts if f"{c['kind']}:{c['id']}" in current]
            raise CommandRefusal("stale_revision", conflicts=shown, current=current)


def run_checks(ctx: CheckContext, spec: CheckSpec, adapter: RulesAdapter) -> None:
    """Checks 4-9 for ``ctx`` (the entry point PE-14b calls)."""

    CheckRunner(spec, adapter).run(ctx)
