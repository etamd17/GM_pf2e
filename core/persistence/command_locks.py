"""The command lock order (contract section 2, "Lock order"; PE-13a).

``CommandLocks`` is bound to one session and one campaign and enforces the
order every command transaction takes its locks in:

1. the campaign stream row (``stream()``, through the required
   ``lock_stream`` callback; PE-14a's ``streams.lock_stream``);
2. the encounter (``encounter()``), which also registers its combatants;
3. action rows (``actions()``; tables register when PE-30's follow-ons add
   them);
4. ``character_states`` rows in ascending ``(character_id, domain)`` order
   (``character_states()``);
5. ``campaign_clocks`` first, then the item, wallet, shop, stash and project
   tables (PE-40+) in their registered order, ids ascending within a table
   (``records_for()``).

Keys are sorted inside each call.  Going back a step, back a step-5 table,
or repeating a step with a smallest key that is not greater than the largest
key already taken at that step raises :class:`LockOrderError`: the order
covers the whole step, not one call.  Every lock reads through the state
repositories' locked read path filtered on the bound campaign, so the
authority, schema-version and ``StateRowMissing`` rules apply under the
lock.  ``allow_stale`` is never passed.

``records`` maps ``(kind, id)`` to the frozen record read under the lock.
``None`` means only "no such key in this campaign" (a character of another
campaign, an encounter that does not exist here, a clock name that is not a
fixed clock); such a key takes no lock and reveals nothing.  Rows that every
writer serializes on the stream row instead (``campaign``, ``session``,
``grant``) enter ``records`` through ``register_serialized`` after
``stream()``.

The wider rule, for PE-14b and PE-14d: every writer takes the
``campaign_streams`` row before the campaign row and before any state or
authorization row.  Only three things may come before it:

* the account-creation advisory lock 73420401 and SQLite ``BEGIN
  IMMEDIATE`` (``core/persistence/identity.py``).  No transaction that holds
  a stream row may take 73420401;
* process locks taken outside the transaction (``_CAMPAIGN_STORE_LOCK``,
  ``SqlWorkflowStore.transaction``'s lock).  No process lock may be taken
  while a stream row is held;
* unlocked reads, such as the invitation locator read that finds the
  campaign id.

Invitation rows are locked only after the stream and campaign rows: the
locator read, then the stream, then the campaign, then the locked invitation
re-read (PE-14d's ``services.lock_invitation_for_write``).  This class does
not model invitations.  ``create_campaign`` and ``campaign_import._import``
take the new campaign row first and then ``lock_stream`` in the same
transaction; they are PE-14d's declared exceptions.

Per-transaction state lives on the instance only.  The module level holds
just the step-3/step-5 table registries and the serialized-kind allowlist.
On SQLite ``FOR UPDATE`` is a no-op; the order checks still run.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from sqlalchemy.orm import Session

from .state_repositories import (
    DOMAIN_KINDS,
    KIND_CLOCK,
    KIND_ENCOUNTER,
    CampaignClockRepository,
    CharacterStateRepository,
    EncounterStateRepository,
    StateRecord,
)

LockStream = Callable[[Session, str], Any]
# ``read(session, campaign_id, id) -> StateRecord | None``, a locked read.
LockedRead = Callable[[Session, str, str], "StateRecord | None"]

STEP_STREAM, STEP_ENCOUNTER, STEP_ACTIONS, STEP_CHARACTERS, STEP_RECORDS = 1, 2, 3, 4, 5


class LockOrderError(RuntimeError):
    """A lock was requested out of the contract's order."""


@dataclass(frozen=True)
class LockTable:
    table: str
    kind: str
    read: LockedRead


def _read_clock(session: Session, campaign_id: str, clock: str) -> StateRecord | None:
    return CampaignClockRepository(session).get(campaign_id, clock, for_update=True)


# Step 5, in lock order.  ``campaign_clocks`` is first; later tables append.
_STEP5_TABLES: list[LockTable] = [LockTable("campaign_clocks", KIND_CLOCK, _read_clock)]
# Step 3 action tables (none yet; PE-30's follow-ons register them).
_ACTION_TABLES: dict[str, LockTable] = {}

STREAM_SERIALIZED_KINDS: frozenset[str] = frozenset({"campaign", "session", "grant"})


def register_lock_table(table: str, *, kind: str, read: LockedRead) -> None:
    """Append a step-5 table after every table registered so far (import time)."""

    if any(entry.table == table for entry in _STEP5_TABLES):
        raise ValueError(f"step-5 table {table} is already registered")
    _STEP5_TABLES.append(LockTable(table, kind, read))


def register_action_table(table: str, *, kind: str, read: LockedRead) -> None:
    """Register a step-3 action table (import time)."""

    if table in _ACTION_TABLES:
        raise ValueError(f"action table {table} is already registered")
    _ACTION_TABLES[table] = LockTable(table, kind, read)


def register_stream_serialized_kind(kind: str) -> None:
    """Allow ``register_serialized`` for a new stream-serialized kind (import time)."""

    global STREAM_SERIALIZED_KINDS
    STREAM_SERIALIZED_KINDS = STREAM_SERIALIZED_KINDS | {kind}


class CommandLocks:
    """Lock manager for one command transaction in one campaign."""

    def __init__(self, session: Session, campaign_id: str, *, lock_stream: LockStream) -> None:
        if not callable(lock_stream):
            raise TypeError("lock_stream must be callable")
        self._session = session
        self._campaign_id = campaign_id
        self._lock_stream = lock_stream
        self._stream_locked = False
        self._stream_row: Any = None
        self._step = 0
        # Largest key requested so far at the current step (and, at step 5,
        # in the current table).
        self._step_max: Any = None
        self._table_index = -1
        self._records: dict[tuple[str, str], StateRecord | None] = {}
        self._records_view = MappingProxyType(self._records)

    @property
    def session(self) -> Session:
        return self._session

    @property
    def campaign_id(self) -> str:
        return self._campaign_id

    @property
    def stream_row(self) -> Any:
        if not self._stream_locked:
            raise LockOrderError("stream() has not run")
        return self._stream_row

    @property
    def records(self) -> Mapping[tuple[str, str], StateRecord | None]:
        return self._records_view

    # -- order bookkeeping -------------------------------------------------

    def _require_stream(self) -> None:
        if not self._stream_locked:
            raise LockOrderError("the stream row must be locked first")

    def _advance(self, step: int, keys: list[Any], table_index: int = 0) -> None:
        """Check ``keys`` (sorted, non-empty) against the order and record them."""

        self._require_stream()
        if step < self._step:
            raise LockOrderError(f"lock step {step} requested after step {self._step}")
        if step == self._step and step == STEP_RECORDS and table_index < self._table_index:
            raise LockOrderError(
                f"{_STEP5_TABLES[table_index].table} requested after "
                f"{_STEP5_TABLES[self._table_index].table}"
            )
        same_run = step == self._step and (
            step != STEP_RECORDS or table_index == self._table_index
        )
        if same_run and keys[0] <= self._step_max:
            raise LockOrderError(
                f"lock step {step} key {keys[0]!r} is not after {self._step_max!r}"
            )
        self._step = step
        self._table_index = table_index if step == STEP_RECORDS else -1
        self._step_max = keys[-1]

    def _enter(self, kind: str, key: str, record: StateRecord | None) -> None:
        self._records[(kind, key)] = record

    # -- steps ---------------------------------------------------------------

    def stream(self) -> Any:
        """Step 1: lock the campaign stream row, exactly once."""

        if self._stream_locked:
            raise LockOrderError("stream() already ran")
        self._stream_row = self._lock_stream(self._session, self._campaign_id)
        self._stream_locked = True
        self._step = STEP_STREAM
        return self._stream_row

    def encounter(self, encounter_id: str) -> StateRecord | None:
        """Step 2: lock the encounter and register its combatants."""

        self._advance(STEP_ENCOUNTER, [encounter_id])
        repository = EncounterStateRepository(self._session)
        record = repository.get(self._campaign_id, encounter_id, for_update=True)
        self._enter(KIND_ENCOUNTER, encounter_id, record)
        if record is not None:
            for combatant in repository.combatants(self._campaign_id, encounter_id):
                self._enter(combatant.kind, combatant.id, combatant)
        return record

    def actions(self, table: str, ids: Iterable[str]) -> dict[str, StateRecord | None]:
        """Step 3: lock rows of a registered action table, ids ascending."""

        entry = _ACTION_TABLES.get(table)
        if entry is None:
            raise LockOrderError(f"{table} is not a registered action table")
        return self._lock_ids(STEP_ACTIONS, entry, ids)

    def character_states(
        self, keys: Iterable[tuple[str, str]]
    ) -> dict[tuple[str, str], StateRecord | None]:
        """Step 4: lock ``(character_id, domain)`` rows in ascending order."""

        ordered = sorted(set(keys))
        for _, domain in ordered:
            if domain not in DOMAIN_KINDS:
                raise ValueError(f"unknown domain {domain!r}")
        if not ordered:
            self._require_stream()
            return {}
        self._advance(STEP_CHARACTERS, ordered)
        repository = CharacterStateRepository(self._session)
        locked: dict[tuple[str, str], StateRecord | None] = {}
        for character_id, domain in ordered:
            record = repository.get(
                self._campaign_id, character_id, domain, for_update=True
            )
            self._enter(DOMAIN_KINDS[domain], character_id, record)
            locked[(character_id, domain)] = record
        return locked

    def records_for(self, table: str, ids: Iterable[str]) -> dict[str, StateRecord | None]:
        """Step 5: lock rows of ``table`` (``campaign_clocks`` or a registered later table)."""

        for index, entry in enumerate(_STEP5_TABLES):
            if entry.table == table:
                return self._lock_ids(STEP_RECORDS, entry, ids, index)
        raise LockOrderError(f"{table} is not a step-5 lock table")

    def _lock_ids(
        self, step: int, entry: LockTable, ids: Iterable[str], table_index: int = 0
    ) -> dict[str, StateRecord | None]:
        ordered = sorted(set(ids))
        if not ordered:
            self._require_stream()
            return {}
        self._advance(step, ordered, table_index)
        locked: dict[str, StateRecord | None] = {}
        for key in ordered:
            record = entry.read(self._session, self._campaign_id, key)
            self._enter(entry.kind, key, record)
            locked[key] = record
        return locked

    def register_serialized(self, kind: str, id: str, record: StateRecord | None) -> None:
        """Enter a stream-serialized row (``campaign``, ``session``, ``grant``, ...).

        ``record`` is ``StateRecord.serialized(kind, id, payload)``, or
        ``None`` for "no such key in this campaign".
        """

        self._require_stream()
        if kind not in STREAM_SERIALIZED_KINDS:
            raise LockOrderError(f"{kind} is not a stream-serialized kind")
        if record is not None and (record.kind != kind or record.id != id):
            raise ValueError("record kind/id do not match the registered key")
        self._enter(kind, id, record)
