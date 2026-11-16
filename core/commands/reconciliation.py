"""Max-HP and pool reconciliation (contract section 2; PE-13c).

A system-neutral hook.  When a build publish, an item change or an effect
change moves a character's effective maximum HP or its pool maxima, the
calling command (PE-15, PE-16-18) runs :func:`reconcile` in the same
transaction and returns its ``entries`` as ``result.reconciliation[]``.

The hook is pure: it reads what a rules adapter reports about the before and
after states, takes no lock and writes nothing.  The command type's
``lock_plan`` has already locked the encounter and the ``character_states``
rows (lock order steps 2 and 4); the caller writes the returned values through
``CharacterStateRepository`` (one ``play`` revision bump) and applies released
reservations to its action rows (PE-30 follow-ons).  :func:`preview` returns
exactly the entries the commit applies, because both call the same function.

The adapter (one per rules profile; this module ships none) provides:

* ``effective_max(build, play, items) -> int`` - the system's effective
  maximum (D&D ``combat.py::effective_max``; PF2e includes drained).  Values
  that carry their own HP rule (PF2e drained, D&D ``max_hp_reduction``) are
  held constant across the before and after calls (C:172-175);
* ``is_dead(play) -> bool``;
* ``pools(build, play, items) -> {pool: max}``;
* ``invalid_choices(build, play) -> [ {"id": ..., ...} ]``;
* ``orphaned_effects(build, play, items, removed_pools) -> [ {"id": ...,
  "pool": <pool or None>, ...} ]`` - effects whose source is gone, including
  those that depended on a removed pool (``pool`` names it).

Entry shapes (``type`` first; PE-15/18b and PE-31 read these keys):

* ``{"type": "hp", "hp_before", "hp_after", "debt_before", "debt_after",
  "effmax_before", "effmax_after"}``, when the maximum, HP or debt moves
  and the character is not dead;
* ``{"type": "reservation_released", "pool", "action_id", "declare_seq",
  "amount", "targets", "reason": "reservation_invalidated"}``;
* ``{"type": "pool_clamped", "pool", "max_before", "max_after",
  "used_before", "used_after"}``;
* ``{"type": "pool_removed", "pool", "used", "effects"}``;
* ``{"type": "effect_ended", "effect_id", "pool", "effect"}``;
* ``{"type": "choice_prompt", "prompt"}``, the prompt being
  ``{"kind": "choice", "origin": "reconciliation", "choice"}``.

Order: the HP entry, then each pool in name order (its releases newest first,
then its clamp or removal), then ended effects, then choice prompts.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol

from sqlalchemy.orm import Session

from core.persistence.state_repositories import EncounterStateRepository

DEBT_KEY = "hp_floor_debt"
PROMPTS_KEY = "choice_prompts"
PROMPT_ORIGIN = "reconciliation"


class ReservationBlocked(Exception):
    """A user-initiated change would truncate a reserved pool (``422 rule_violation``)."""

    code = "rule_violation"
    http_status = 422

    def __init__(self, action_ids: Iterable[str]) -> None:
        # Each blocking action once, at its first position (an action may
        # hold reservations in several pools).
        self.action_ids = tuple(dict.fromkeys(action_ids))
        super().__init__(self.code)


class InEncounter(Exception):
    """A build publish for a combatant in the active encounter (``422 in_encounter``)."""

    code = "in_encounter"
    http_status = 422

    def __init__(self, character_id: str) -> None:
        self.character_id = character_id
        super().__init__(self.code)


class RulesAdapter(Protocol):
    def effective_max(self, build: Any, play: Mapping[str, Any], items: Any) -> int: ...
    def is_dead(self, play: Mapping[str, Any]) -> bool: ...
    def pools(self, build: Any, play: Mapping[str, Any], items: Any) -> Mapping[str, int]: ...
    def invalid_choices(self, build: Any, play: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]: ...
    def orphaned_effects(self, build: Any, play: Mapping[str, Any], items: Any,
                         removed_pools: frozenset[str]) -> Iterable[Mapping[str, Any]]: ...


@dataclass(frozen=True)
class Reservation:
    """An open action's hold on a pool (PE-30 follow-ons own the rows)."""

    pool: str
    action_id: str
    declare_seq: int
    amount: int
    targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class Snapshot:
    """One side of the change: the build and the items the adapter reads."""

    build: Any
    items: Any = None


@dataclass(frozen=True)
class Reconciliation:
    """The entries, and the values the caller writes."""

    entries: tuple[dict[str, Any], ...]
    hp: int
    hp_floor_debt: int
    used: dict[str, int]
    dropped_pools: tuple[str, ...]
    released: tuple[Reservation, ...]
    ended_effects: tuple[Any, ...]
    prompts: tuple[dict[str, Any], ...] = field(default=())

    @property
    def released_actions(self) -> tuple[str, ...]:
        """Each released action once, in release order.

        ``released`` has one item per reservation, so an action holding
        several pools appears once per pool there.
        """

        return tuple(dict.fromkeys(r.action_id for r in self.released))

    def play_updates(self, play: Mapping[str, Any]) -> dict[str, Any]:
        """The ``play`` payload keys this hook owns (debt and stored prompts).

        HP and pool counts are stored under system-specific keys; the caller's
        adapter writes ``hp`` and ``used`` itself.
        """

        return {DEBT_KEY: self.hp_floor_debt,
                PROMPTS_KEY: [copy.deepcopy(p) for p in play.get(PROMPTS_KEY) or ()]
                             + [copy.deepcopy(p) for p in self.prompts]}


def _count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer, not {value!r}")
    return value


def reconcile_hp(hp: int, debt: int, effmax_before: int, effmax_after: int,
                 dead: bool) -> tuple[int, int]:
    """New ``(hp, hp_floor_debt)`` after the effective maximum moves (C:176-194).

    The rule runs only for a change that moves the maximum (C:168-170): with
    the maximum unchanged nothing changes, so an outstanding debt is never
    charged as damage by a publish or pool change that leaves the maximum alone.
    """

    hp, debt = _count(hp, "hp"), _count(debt, "hp_floor_debt")
    _count(effmax_before, "effmax_before"), _count(effmax_after, "effmax_after")
    if dead or effmax_before == effmax_after:
        return hp, debt
    if hp == 0:
        return 0, 0
    raw = hp + (effmax_after - effmax_before) - debt
    if raw >= 1:
        return min(raw, effmax_after), 0
    return 1, 1 - raw


def debt_after_heal(hp_after: int, debt: int, rest_to_max: bool) -> int:
    """The debt after a heal or rest: cleared at 0 HP or a rest to max (C:189-190)."""

    _count(hp_after, "hp_after")
    return 0 if hp_after == 0 or rest_to_max else _count(debt, "hp_floor_debt")


def _pools(before: Mapping[str, int], after: Mapping[str, int], used: Mapping[str, int],
           reservations: Iterable[Reservation], user_initiated: bool):
    held: dict[str, list[Reservation]] = {}
    for reservation in reservations:
        if reservation.pool not in before:
            raise ValueError(f"reservation {reservation.action_id} holds unknown pool "
                             f"{reservation.pool}")
        _count(reservation.amount, "reservation amount")
        held.setdefault(reservation.pool, []).append(reservation)
    for pool in used:
        if pool not in before:
            raise ValueError(f"used count for unknown pool {pool}")

    if user_initiated:
        blocking = []
        for pool, holds in held.items():
            in_use = _count(used.get(pool, 0), f"used[{pool}]") + sum(r.amount for r in holds)
            if pool not in after or after[pool] < in_use:
                blocking.extend(holds)
        if blocking:
            ordered = sorted(blocking, key=lambda r: (r.declare_seq, r.action_id))
            raise ReservationBlocked(r.action_id for r in ordered)

    entries, new_used, dropped, released = [], {}, [], []
    for pool in sorted(before):
        count = _count(used.get(pool, 0), f"used[{pool}]")
        holds = sorted(held.get(pool, ()), key=lambda r: (r.declare_seq, r.action_id),
                       reverse=True)
        maximum = after.get(pool)
        reserved = sum(r.amount for r in holds)
        for reservation in holds:
            if maximum is not None and count + reserved <= maximum:
                break
            reserved -= reservation.amount
            released.append(reservation)
            entries.append({"type": "reservation_released", "pool": pool,
                            "action_id": reservation.action_id,
                            "declare_seq": reservation.declare_seq,
                            "amount": reservation.amount,
                            "targets": list(reservation.targets),
                            "reason": "reservation_invalidated"})
        if maximum is None:
            dropped.append(pool)
            entries.append({"type": "pool_removed", "pool": pool, "used": count,
                            "effects": []})
            continue
        _count(maximum, f"pools[{pool}]")
        if count > maximum:
            entries.append({"type": "pool_clamped", "pool": pool,
                            "max_before": before[pool], "max_after": maximum,
                            "used_before": count, "used_after": maximum})
            count = maximum
        new_used[pool] = count
    for pool in sorted(set(after) - set(before)):
        new_used[pool] = 0
    return entries, new_used, dropped, released


def reconcile(adapter: RulesAdapter, *, before: Snapshot, after: Snapshot,
              play: Mapping[str, Any], hp: int, used: Mapping[str, int],
              reservations: Iterable[Reservation] = (),
              user_initiated: bool) -> Reconciliation:
    """Every HP, debt, pool, reservation, effect and prompt change, in commit order.

    ``play`` is the locked ``play`` payload; ``hp`` and ``used`` are its
    current HP and pool counts, read by the caller's adapter.  A
    user-initiated change that would truncate a reserved pool raises
    :class:`ReservationBlocked` before anything is computed; ``override``
    is not an input, so it cannot bypass this (C:203-204).
    """

    debt = play.get(DEBT_KEY, 0)
    effmax_before = adapter.effective_max(before.build, play, before.items)
    effmax_after = adapter.effective_max(after.build, play, after.items)
    pool_entries, new_used, dropped, released = _pools(
        dict(adapter.pools(before.build, play, before.items)),
        dict(adapter.pools(after.build, play, after.items)),
        used, list(reservations), user_initiated)

    dead = bool(adapter.is_dead(play))
    new_hp, new_debt = reconcile_hp(hp, debt, effmax_before, effmax_after, dead)
    entries: list[dict[str, Any]] = []
    if not dead and ((new_hp, new_debt) != (hp, debt) or effmax_before != effmax_after):
        entries.append({"type": "hp", "hp_before": hp, "hp_after": new_hp,
                        "debt_before": debt, "debt_after": new_debt,
                        "effmax_before": effmax_before, "effmax_after": effmax_after})

    effects = [copy.deepcopy(dict(e)) for e in adapter.orphaned_effects(
        after.build, play, after.items, frozenset(dropped))]
    for entry in pool_entries:
        if entry["type"] == "pool_removed":
            entry["effects"] = [e["id"] for e in effects if e.get("pool") == entry["pool"]]
    entries.extend(pool_entries)
    entries.extend({"type": "effect_ended", "effect_id": e["id"], "pool": e.get("pool"),
                    "effect": e} for e in effects)

    stored = {(p.get("choice") or {}).get("id") for p in play.get(PROMPTS_KEY) or ()
              if p.get("origin") == PROMPT_ORIGIN}
    prompts = []
    for choice in adapter.invalid_choices(after.build, play):
        if choice["id"] in stored:
            continue  # already waiting for the player; never duplicated or removed
        stored.add(choice["id"])
        prompt = {"kind": "choice", "origin": PROMPT_ORIGIN, "choice": copy.deepcopy(dict(choice))}
        prompts.append(prompt)
        entries.append({"type": "choice_prompt", "prompt": copy.deepcopy(prompt)})

    return Reconciliation(entries=tuple(entries), hp=new_hp, hp_floor_debt=new_debt,
                          used=new_used, dropped_pools=tuple(dropped),
                          released=tuple(released),
                          ended_effects=tuple(e["id"] for e in effects),
                          prompts=tuple(prompts))


def apply(adapter: RulesAdapter, **kwargs: Any) -> Reconciliation:
    """The commit form: the values to write, with the entries for the result."""

    return reconcile(adapter, **kwargs)


def preview(adapter: RulesAdapter, **kwargs: Any) -> list[dict[str, Any]]:
    """Exactly the entries :func:`apply` would commit (C:220)."""

    return [copy.deepcopy(e) for e in reconcile(adapter, **kwargs).entries]


def refuse_publish_in_encounter(session: Session, campaign_id: str, character_id: str,
                                override: Any) -> None:
    """Refuse a build publish for a combatant of the active encounter, unless overridden.

    Whether the principal may send ``override`` is check 4's (PE-13b); this
    trusts the flag it is given (C:218-219).
    """

    if override is not None:
        return
    if EncounterStateRepository(session).is_combatant(campaign_id, character_id):
        raise InEncounter(character_id)
