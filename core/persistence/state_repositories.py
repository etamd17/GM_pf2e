"""Campaign-scoped repositories for the PE-12 game-state rows (PE-13a).

Nothing in the product calls these yet.  They give later command tasks one
way to read, lock and write ``character_states``, ``encounter_states`` (with
their combatants) and ``campaign_clocks`` under the accepted contract
(``docs/player-release/state-command-contract.md``):

* Every method takes ``campaign_id`` and filters on it.  A key that belongs
  to another campaign reads exactly like a key that does not exist.
* Revisions (section 2).  An inserted row starts at revision 1.  Every write
  is a compare-and-set ``UPDATE ... WHERE <key> AND revision = :read`` that
  sets ``revision = :read + 1`` and ``updated_at`` explicitly; a stale
  ``read_revision`` raises :class:`RevisionConflict` and writes nothing.  No
  method sets or lowers a revision.  A combatant change bumps its encounter's
  revision once; a character's ``play`` row never touches the encounter.
* Authority (section 7).  No ``campaign_state_authority`` row means ``file``.
  Reads need ``db`` or ``exporting``, or ``allow_stale=True`` (the re-import
  and the populated-downgrade check only).  Writes need ``db``.  In
  ``db``/``exporting`` a missing row for a key that exists in the campaign
  raises :class:`StateRowMissing`; files are never read.
* Pins (section 1).  The pin is ``campaigns.settings["rules"]``.  A
  character-state or encounter row whose ``rules_profile`` would differ from
  the pin's profile raises :class:`ProfileMismatch` before any write.
* Schema versions (section 2).  Reads upgrade older payloads in memory and
  never write; writes store the current version; a newer stored version
  raises :class:`UnknownSchemaVersion`.
* No request caches (section 4 step 4): everything goes through the caller's
  ``Session``; nothing touches ``flask.g``.

Reads use Core statements on the tables, not ORM entities, so a record is
what the database holds now, never a stale identity-map object.  Records are
frozen and hold deep copies of the stored JSON.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.orm import Session

from .models import (
    RULES_PROFILES,
    Campaign,
    CampaignClock,
    CampaignStateAuthority,
    Character,
    CharacterState,
    EncounterCombatant,
    EncounterState,
    utc_now,
)

# Record kinds (contract section 2).  The revision kinds are the ones PE-13b
# accepts in ``expected``.  ``combatant`` and the stream-serialized kinds
# (``campaign``, ``session``, ``grant``; see ``command_locks``) are record
# kinds only, with no revision of their own.
KIND_BUILD = "character.build"
KIND_PLAY = "character.play"
KIND_ENCOUNTER = "encounter"
KIND_COMBATANT = "combatant"
KIND_CLOCK = "clock"
REVISION_KINDS = frozenset({KIND_BUILD, KIND_PLAY, KIND_ENCOUNTER, KIND_CLOCK})
DOMAIN_KINDS = {"build": KIND_BUILD, "play": KIND_PLAY}
FIXED_CLOCKS = ("world", "downtime")
READABLE_AUTHORITIES = frozenset({"db", "exporting"})


class StateRepositoryError(Exception):
    """Base class for state-repository refusals."""


class AuthorityMismatch(StateRepositoryError):
    """The campaign's authority does not allow this read or write."""


class StateRowMissing(StateRepositoryError):
    """In db/exporting authority, the row for an existing key is missing."""


class RevisionConflict(StateRepositoryError):
    """A compare-and-set lost: the row changed (or vanished) since it was read."""


class ProfileMismatch(StateRepositoryError):
    """A row's rules profile would differ from the campaign pin."""


class PinUnavailable(StateRepositoryError):
    """The campaign has no well-formed ``settings["rules"]`` pin."""


class UnknownSchemaVersion(StateRepositoryError):
    """A stored schema version is newer than this code, or has no upgrade."""


# Current schema version per kind (1 today), and the in-memory upgrades:
# ``SCHEMA_UPGRADES[kind][v](payload)`` returns the payload at ``v + 1``.
CURRENT_SCHEMA_VERSIONS: dict[str, int] = {KIND_BUILD: 1, KIND_PLAY: 1, KIND_ENCOUNTER: 1}
SCHEMA_UPGRADES: dict[str, dict[int, Callable[[dict], dict]]] = {}


def register_schema_upgrade(kind: str, from_version: int, upgrade: Callable[[dict], dict]) -> None:
    steps = SCHEMA_UPGRADES.setdefault(kind, {})
    if from_version in steps:
        raise ValueError(f"upgrade for {kind} v{from_version} already registered")
    steps[from_version] = upgrade


def upgrade_payload(kind: str, version: int, payload: dict) -> tuple[int, dict]:
    """Return ``(current_version, payload)``, upgrading in memory only."""

    current, version = CURRENT_SCHEMA_VERSIONS[kind], int(version)
    if version > current:
        raise UnknownSchemaVersion(f"{kind} schema_version {version} is newer than {current}")
    while version < current:
        step = SCHEMA_UPGRADES.get(kind, {}).get(version)
        if step is None:
            raise UnknownSchemaVersion(f"no upgrade for {kind} from v{version}")
        payload, version = step(payload), version + 1
    return version, payload


@dataclass(frozen=True)
class StateRecord:
    """A frozen read of one row (contract section 2 shape).

    ``id`` is the key within its kind: the character id, encounter id,
    combatant id or clock name.  A combatant carries its encounter's
    revision.  Stream-serialized rows (``serialized``) have no revision,
    schema version, profile or content.
    """

    kind: str
    id: str
    revision: int | None
    schema_version: int | None
    rules_profile: str | None
    content: dict[str, Any] | None
    payload: dict[str, Any]

    @classmethod
    def serialized(cls, kind: str, id: str, payload: Mapping[str, Any]) -> "StateRecord":
        return cls(kind, id, None, None, None, None, copy.deepcopy(dict(payload)))


class StateAuthorityRepository:
    """Read-only view of ``campaign_state_authority`` (no row = ``file``)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def _row(self, campaign_id: str):
        t = CampaignStateAuthority.__table__
        return self.session.execute(
            select(t.c.authority, t.c.stream_epoch).where(t.c.campaign_id == campaign_id)
        ).first()

    def authority(self, campaign_id: str) -> str:
        row = self._row(campaign_id)
        return "file" if row is None else row.authority

    def stream_epoch(self, campaign_id: str) -> int | None:
        row = self._row(campaign_id)
        return None if row is None else int(row.stream_epoch)


class CampaignPinRepository:
    """The campaign's rules pin, ``campaigns.settings["rules"]``."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def pin(self, campaign_id: str) -> dict[str, Any]:
        t = Campaign.__table__
        settings = self.session.scalar(select(t.c.settings).where(t.c.id == campaign_id))
        rules = settings.get("rules") if isinstance(settings, dict) else None
        if (
            not isinstance(rules, dict)
            or rules.get("profile") not in RULES_PROFILES
            or not isinstance(rules.get("content"), dict)
        ):
            raise PinUnavailable(f"campaign {campaign_id} has no valid rules pin")
        return {"profile": rules["profile"], "content": copy.deepcopy(rules["content"])}


class _Repository:
    table: Any

    def __init__(self, session: Session) -> None:
        self.session = session

    def _check_read(self, campaign_id: str, allow_stale: bool) -> str:
        authority = StateAuthorityRepository(self.session).authority(campaign_id)
        if authority not in READABLE_AUTHORITIES and not allow_stale:
            raise AuthorityMismatch(f"campaign {campaign_id} state is {authority}-authoritative")
        return authority

    def _check_write(self, campaign_id: str, rules_profile: str | None = None) -> None:
        authority = StateAuthorityRepository(self.session).authority(campaign_id)
        if authority != "db":
            raise AuthorityMismatch(
                f"campaign {campaign_id} state is {authority}-authoritative; writes need db"
            )
        if rules_profile is not None:
            pinned = CampaignPinRepository(self.session).pin(campaign_id)["profile"]
            if rules_profile != pinned:
                raise ProfileMismatch(
                    f"rules_profile {rules_profile!r} differs from pin {pinned!r}")

    def _key(self, campaign_id: str, **columns: Any):
        t = self.table
        return and_(t.c.campaign_id == campaign_id, *(t.c[k] == v for k, v in columns.items()))

    def _one(self, key, *, for_update: bool = False):
        statement = select(self.table).where(key)
        if for_update:
            statement = statement.with_for_update()
        return self.session.execute(statement).first()

    def _all(self, campaign_id: str, *order: str):
        t = self.table
        statement = select(t).where(t.c.campaign_id == campaign_id)
        return self.session.execute(statement.order_by(*(t.c[c] for c in order))).all()

    def _stored(self, key, kind: str, read_revision: int):
        """The row a write replaces, refusing a stale or newer-version row first."""

        row = self._one(key)
        if row is None or int(row.revision) != int(read_revision):
            raise RevisionConflict("row changed or is missing since it was read")
        if int(row.schema_version) > CURRENT_SCHEMA_VERSIONS[kind]:
            raise UnknownSchemaVersion(f"{kind} schema_version {row.schema_version} is newer")
        return row

    def _cas(self, key, read_revision: int, values: dict[str, Any]) -> None:
        """``UPDATE ... WHERE key AND revision = :read`` setting revision + 1."""

        values = dict(values, revision=int(read_revision) + 1, updated_at=utc_now())
        stale = self.table.c.revision == int(read_revision)
        result = self.session.execute(update(self.table).where(key, stale).values(values))
        if result.rowcount != 1:
            raise RevisionConflict("row changed or is missing since it was read")


def _domain_kind(domain: str) -> str:
    if domain not in DOMAIN_KINDS:
        raise ValueError(f"unknown domain {domain!r}")
    return DOMAIN_KINDS[domain]


class CharacterStateRepository(_Repository):
    table = CharacterState.__table__

    @staticmethod
    def _record(row) -> StateRecord:
        kind = DOMAIN_KINDS[row.domain]
        version, payload = upgrade_payload(kind, row.schema_version, copy.deepcopy(row.payload))
        return StateRecord(kind, row.character_id, int(row.revision), version,
                           row.rules_profile, copy.deepcopy(row.content), payload)

    def _character_exists(self, campaign_id: str, character_id: str) -> bool:
        t = Character.__table__
        found = select(t.c.id).where(t.c.campaign_id == campaign_id, t.c.id == character_id)
        return self.session.scalar(found) is not None

    def get(self, campaign_id: str, character_id: str, domain: str, *,
            for_update: bool = False, allow_stale: bool = False) -> StateRecord | None:
        """The row, or ``None`` when the character is not in this campaign.

        In db/exporting authority a missing row for a character of this
        campaign raises :class:`StateRowMissing`.
        """

        kind = _domain_kind(domain)
        authority = self._check_read(campaign_id, allow_stale)
        row = self._one(self._key(campaign_id, character_id=character_id, domain=domain),
                        for_update=for_update)
        if row is not None:
            return self._record(row)
        if authority in READABLE_AUTHORITIES and self._character_exists(campaign_id, character_id):
            raise StateRowMissing(f"{kind} row missing for character {character_id}")
        return None

    def list(self, campaign_id: str, *, allow_stale: bool = False) -> list[StateRecord]:
        self._check_read(campaign_id, allow_stale)
        return [self._record(row) for row in self._all(campaign_id, "character_id", "domain")]

    def insert(self, campaign_id: str, character_id: str, domain: str, *, rules_profile: str,
               content: Mapping[str, Any], payload: Mapping[str, Any]) -> StateRecord:
        kind = _domain_kind(domain)
        self._check_write(campaign_id, rules_profile)
        if not self._character_exists(campaign_id, character_id):
            raise StateRepositoryError(f"character {character_id} is not in this campaign")
        now = utc_now()
        self.session.execute(insert(self.table).values(
            campaign_id=campaign_id, character_id=character_id, domain=domain,
            schema_version=CURRENT_SCHEMA_VERSIONS[kind], revision=1,
            rules_profile=rules_profile, content=copy.deepcopy(dict(content)),
            payload=copy.deepcopy(dict(payload)), created_at=now, updated_at=now,
        ))
        return self.get(campaign_id, character_id, domain)

    def write(self, campaign_id: str, character_id: str, domain: str, *, read_revision: int,
              payload: Mapping[str, Any], content: Mapping[str, Any] | None = None,
              rules_profile: str | None = None) -> StateRecord:
        """Replace the payload (and optionally content/profile) at ``read_revision + 1``."""

        kind = _domain_kind(domain)
        self._check_write(campaign_id)
        key = self._key(campaign_id, character_id=character_id, domain=domain)
        rules_profile = rules_profile or self._stored(key, kind, read_revision).rules_profile
        self._check_write(campaign_id, rules_profile)
        values = {"schema_version": CURRENT_SCHEMA_VERSIONS[kind], "rules_profile": rules_profile,
                  "payload": copy.deepcopy(dict(payload))}
        if content is not None:
            values["content"] = copy.deepcopy(dict(content))
        self._cas(key, read_revision, values)
        return self.get(campaign_id, character_id, domain)

    def delete(self, campaign_id: str, character_id: str, domain: str, *,
               read_revision: int) -> None:
        _domain_kind(domain)
        self._check_write(campaign_id)
        key = self._key(campaign_id, character_id=character_id, domain=domain)
        result = self.session.execute(
            delete(self.table).where(key, self.table.c.revision == int(read_revision)))
        if result.rowcount != 1:
            raise RevisionConflict("row changed or is missing since it was read")


_ENCOUNTER_FIELDS = ("status", "round", "turn", "initiative", "payload", "clock_anchors")
_COMBATANT_FIELDS = ("kind", "character_id", "creature_ref", "legacy_ref")


class EncounterStateRepository(_Repository):
    """Encounters and their combatants; combatants share the encounter revision."""

    table = EncounterState.__table__
    combatants_table = EncounterCombatant.__table__

    @staticmethod
    def _record(row) -> StateRecord:
        state = {name: copy.deepcopy(getattr(row, name)) for name in _ENCOUNTER_FIELDS}
        version, state = upgrade_payload(KIND_ENCOUNTER, row.schema_version, state)
        return StateRecord(KIND_ENCOUNTER, row.encounter_id, int(row.revision), version,
                           row.rules_profile, copy.deepcopy(row.content), state)

    def get(self, campaign_id: str, encounter_id: str, *, for_update: bool = False,
            allow_stale: bool = False) -> StateRecord | None:
        self._check_read(campaign_id, allow_stale)
        row = self._one(self._key(campaign_id, encounter_id=encounter_id), for_update=for_update)
        return None if row is None else self._record(row)

    def active(self, campaign_id: str, *, allow_stale: bool = False) -> StateRecord | None:
        self._check_read(campaign_id, allow_stale)
        row = self._one(self._key(campaign_id, status="active"))
        return None if row is None else self._record(row)

    def list(self, campaign_id: str, *, allow_stale: bool = False) -> list[StateRecord]:
        self._check_read(campaign_id, allow_stale)
        return [self._record(row) for row in self._all(campaign_id, "encounter_id")]

    def _combatant_query(self, campaign_id: str):
        c, e = self.combatants_table, self.table
        joined = c.join(e, and_(e.c.campaign_id == c.c.campaign_id,
                                e.c.encounter_id == c.c.encounter_id))
        return (select(c, e.c.revision.label("encounter_revision"))
                .select_from(joined).where(c.c.campaign_id == campaign_id))

    def _combatants(self, campaign_id: str, *where) -> list[StateRecord]:
        c = self.combatants_table
        rows = self.session.execute(self._combatant_query(campaign_id).where(*where)
                                    .order_by(c.c.encounter_id, c.c.combatant_id)).all()
        return [StateRecord(KIND_COMBATANT, row.combatant_id, int(row.encounter_revision), None,
                            None, None, {"encounter_id": row.encounter_id,
                                         **{n: getattr(row, n) for n in _COMBATANT_FIELDS}})
                for row in rows]

    def combatants(self, campaign_id: str, encounter_id: str, *,
                   allow_stale: bool = False) -> list[StateRecord]:
        self._check_read(campaign_id, allow_stale)
        return self._combatants(campaign_id, self.combatants_table.c.encounter_id == encounter_id)

    def list_combatants(self, campaign_id: str, *, allow_stale: bool = False) -> list[StateRecord]:
        self._check_read(campaign_id, allow_stale)
        return self._combatants(campaign_id)

    def is_combatant(self, campaign_id: str, character_id: str, *,
                     allow_stale: bool = False) -> bool:
        """True only for a PC combatant of the campaign's active encounter."""

        self._check_read(campaign_id, allow_stale)
        c = self.combatants_table
        return bool(self._combatants(campaign_id, c.c.character_id == character_id,
                                     c.c.kind == "pc", self.table.c.status == "active"))

    def insert(self, campaign_id: str, encounter_id: str, *, rules_profile: str,
               content: Mapping[str, Any], status: str = "active", round: int = 0,
               turn: int = 0, initiative: Iterable[Any] = (),
               payload: Mapping[str, Any] | None = None,
               clock_anchors: Mapping[str, Any] | None = None) -> StateRecord:
        self._check_write(campaign_id, rules_profile)
        now = utc_now()
        self.session.execute(insert(self.table).values(
            campaign_id=campaign_id, encounter_id=encounter_id, status=status,
            round=int(round), turn=int(turn), initiative=copy.deepcopy(list(initiative)),
            payload=copy.deepcopy(dict(payload or {})),
            clock_anchors=copy.deepcopy(dict(clock_anchors or {})),
            rules_profile=rules_profile, content=copy.deepcopy(dict(content)),
            schema_version=CURRENT_SCHEMA_VERSIONS[KIND_ENCOUNTER], revision=1,
            created_at=now, updated_at=now,
        ))
        return self.get(campaign_id, encounter_id)

    def _bump(self, campaign_id: str, encounter_id: str, read_revision: int,
              values: dict[str, Any]) -> None:
        """Compare-and-set the encounter row at ``read_revision + 1``.

        Every encounter column is written: the stored state is upgraded to
        the current schema version in memory and the caller's fields are laid
        over it, so a partial write or a combatant bump never labels an old
        payload with the new version.
        """

        self._check_write(campaign_id)
        key = self._key(campaign_id, encounter_id=encounter_id)
        stored = self._record(self._stored(key, KIND_ENCOUNTER, read_revision))
        self._check_write(campaign_id, values.get("rules_profile") or stored.rules_profile)
        values = dict(stored.payload, **values, schema_version=stored.schema_version)
        self._cas(key, read_revision, values)

    def write(self, campaign_id: str, encounter_id: str, *, read_revision: int,
              **fields: Any) -> StateRecord:
        """Update ``status``, ``round``, ``turn``, ``initiative``, ``payload``,
        ``clock_anchors``, ``content`` or ``rules_profile`` at ``read_revision + 1``."""

        unknown = set(fields) - set(_ENCOUNTER_FIELDS) - {"content", "rules_profile"}
        if unknown:
            raise ValueError(f"unknown encounter fields: {sorted(unknown)}")
        self._bump(campaign_id, encounter_id, read_revision,
                   {name: copy.deepcopy(value) for name, value in fields.items()})
        return self.get(campaign_id, encounter_id)

    def _combatant_key(self, campaign_id: str, encounter_id: str, combatant_id: str):
        c = self.combatants_table
        key = and_(c.c.campaign_id == campaign_id, c.c.encounter_id == encounter_id,
                   c.c.combatant_id == combatant_id)
        if self.session.execute(select(c.c.combatant_id).where(key)).first() is None:
            raise StateRepositoryError(f"combatant {combatant_id} is not in {encounter_id}")
        return key

    def add_combatant(self, campaign_id: str, encounter_id: str, *, read_revision: int,
                      combatant_id: str, kind: str, character_id: str | None = None,
                      creature_ref: str | None = None,
                      legacy_ref: str | None = None) -> StateRecord:
        """Insert a combatant and bump the encounter revision once."""

        self._bump(campaign_id, encounter_id, read_revision, {})
        now = utc_now()
        self.session.execute(insert(self.combatants_table).values(
            campaign_id=campaign_id, encounter_id=encounter_id, combatant_id=combatant_id,
            kind=kind, character_id=character_id, creature_ref=creature_ref,
            legacy_ref=legacy_ref, created_at=now, updated_at=now,
        ))
        return self.get(campaign_id, encounter_id)

    def update_combatant(self, campaign_id: str, encounter_id: str, *, read_revision: int,
                         combatant_id: str, **fields: Any) -> StateRecord:
        """Change combatant fields and bump the encounter revision once."""

        unknown = set(fields) - set(_COMBATANT_FIELDS)
        if unknown:
            raise ValueError(f"unknown combatant fields: {sorted(unknown)}")
        self._check_write(campaign_id)
        key = self._combatant_key(campaign_id, encounter_id, combatant_id)
        self._bump(campaign_id, encounter_id, read_revision, {})
        self.session.execute(update(self.combatants_table).where(key)
                             .values(updated_at=utc_now(), **fields))
        return self.get(campaign_id, encounter_id)

    def remove_combatant(self, campaign_id: str, encounter_id: str, *, read_revision: int,
                         combatant_id: str) -> StateRecord:
        """Delete a combatant and bump the encounter revision once."""

        self._check_write(campaign_id)
        key = self._combatant_key(campaign_id, encounter_id, combatant_id)
        self._bump(campaign_id, encounter_id, read_revision, {})
        self.session.execute(delete(self.combatants_table).where(key))
        return self.get(campaign_id, encounter_id)


class CampaignClockRepository(_Repository):
    """The fixed ``world`` and ``downtime`` clocks (contract section 2)."""

    table = CampaignClock.__table__

    @staticmethod
    def _record(row) -> StateRecord:
        return StateRecord(KIND_CLOCK, row.clock, int(row.revision), None, None, None,
                           {"value": int(row.value)})

    @staticmethod
    def _fixed(clock: str) -> None:
        if clock not in FIXED_CLOCKS:
            raise ValueError(f"unknown clock {clock!r}")

    def get(self, campaign_id: str, clock: str, *, for_update: bool = False,
            allow_stale: bool = False) -> StateRecord | None:
        """The clock, or ``None`` for a name that is not a fixed clock.

        In db/exporting authority a missing ``world``/``downtime`` row raises
        :class:`StateRowMissing`.
        """

        authority = self._check_read(campaign_id, allow_stale)
        if clock not in FIXED_CLOCKS:
            return None
        row = self._one(self._key(campaign_id, clock=clock), for_update=for_update)
        if row is not None:
            return self._record(row)
        if authority in READABLE_AUTHORITIES:
            raise StateRowMissing(f"clock {clock} row missing")
        return None

    def list(self, campaign_id: str, *, allow_stale: bool = False) -> list[StateRecord]:
        self._check_read(campaign_id, allow_stale)
        return [self._record(row) for row in self._all(campaign_id, "clock")]

    def insert(self, campaign_id: str, clock: str, *, value: int = 0) -> StateRecord:
        self._fixed(clock)
        self._check_write(campaign_id)
        self.session.execute(insert(self.table).values(
            campaign_id=campaign_id, clock=clock, value=int(value), revision=1,
            updated_at=utc_now()))
        return self.get(campaign_id, clock)

    def write(self, campaign_id: str, clock: str, *, read_revision: int,
              value: int) -> StateRecord:
        self._fixed(clock)
        self._check_write(campaign_id)
        self._cas(self._key(campaign_id, clock=clock), read_revision, {"value": int(value)})
        return self.get(campaign_id, clock)
