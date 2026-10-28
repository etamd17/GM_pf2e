"""Mistborn bestiary rows and tracker actor documents from pinned catalog records.

The Mistborn adversaries live in the private rules bundle as ``adversary``
records whose ``actions`` reference separate ``action`` records. The tracker,
statblock and encounter autosave speak the historical Foundry actor shape that
``CosmereActor`` reads, so this module projects one into the other. It reads
only what the record states; nothing is inferred (an action with no structured
attack stays a described ability rather than becoming a guessed strike).
"""
from __future__ import annotations

from collections.abc import Mapping

ADVERSARY_PREFIX = "mistborn.adversary."
_ACTION_COSTS = {"one": ("action", 1), "two": ("action", 2), "three": ("action", 3),
                 "reaction": ("reaction", 1), "free": ("free", 0)}
_STATISTIC_KEYS = {"strength": "str", "speed": "spd", "intellect": "int",
                   "willpower": "wil", "awareness": "awa", "presence": "pre"}


def _data(record):
    parameters = record.get("parameters") if isinstance(record.get("parameters"), Mapping) else {}
    data = parameters.get("data")
    return data if isinstance(data, Mapping) else {}


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return int(default)


def is_catalog_adversary_id(value):
    return isinstance(value, str) and value.startswith(ADVERSARY_PREFIX) \
        and ".action." not in value


def bestiary_rows(catalog):
    """Bestiary cards for every adversary record the profile's catalog shows."""
    rows = []
    for record in (catalog.get("catalogs") or {}).get("adversary", []):
        if not isinstance(record, Mapping) or not is_catalog_adversary_id(record.get("id")):
            continue
        data = _data(record)
        rows.append({"id": record["id"], "name": record.get("name") or record["id"],
                     "tier": data.get("tier"), "role": data.get("role"),
                     "size": data.get("size")})
    rows.sort(key=lambda a: ((a["tier"] or 0), (a["name"] or "").lower()))
    return rows


def _health(data):
    health = data.get("health")
    if isinstance(health, Mapping):
        return max(0, _int(health.get("default"), 0))
    return max(0, _int(health, 0))


def _action_item(action_id, record):
    if record is None:
        # The adversary names an action this catalog cannot show; keep the slot
        # visible rather than silently dropping part of the statblock.
        return {"_id": action_id, "type": "action", "name": action_id.rsplit(".", 1)[-1]
                .replace("-", " ").title(),
                "system": {"id": action_id, "activation": {"type": "utility",
                                                           "cost": {"type": "free", "value": 0}},
                           "description": {"short": "Not available in the installed rules."}}}
    data = _data(record)
    cost_type, cost_value = _ACTION_COSTS.get(data.get("action_cost"), ("free", 0))
    return {"_id": action_id, "type": "action", "name": record.get("name") or action_id,
            "system": {"id": action_id,
                       "activation": {"type": "utility",
                                      "cost": {"type": cost_type, "value": cost_value}},
                       "description": {"short": record.get("summary") or ""}}}


def actor_document(record, actions_by_id):
    """A ``CosmereActor``-readable document for one catalog adversary record."""
    data = _data(record)
    stats = data.get("statistics") if isinstance(data.get("statistics"), Mapping) else {}
    health = _health(data)
    focus = max(0, _int(data.get("focus"), 0))
    investiture = max(0, _int(data.get("investiture"), 0))
    defenses = {key: {"useOverride": True, "override": _int(stats.get(source), 10)}
                for key, source in (("phy", "physical_defense"), ("cog", "cognitive_defense"),
                                    ("spi", "spiritual_defense"))}
    features = [row if isinstance(row, str) else (row.get("name") if isinstance(row, Mapping) else None)
                for row in (data.get("features") or [])]
    items = [{"_id": f"{record['id']}.feature.{index}", "type": "trait", "name": name, "system": {}}
             for index, name in enumerate(features) if isinstance(name, str) and name]
    for action_id in data.get("actions") or []:
        if isinstance(action_id, str):
            items.append(_action_item(action_id, actions_by_id.get(action_id)))
    return {
        "_id": record["id"], "name": record.get("name") or record["id"], "type": "adversary",
        "system": {
            "level": {"value": max(1, _int(data.get("level"), 1))},
            "tier": data.get("tier"), "role": data.get("role"), "size": data.get("size"),
            "type": data.get("type"),
            "attributes": {short: {"value": _int(stats.get(long), 0)}
                           for long, short in _STATISTIC_KEYS.items()},
            "defenses": defenses,
            "resources": {
                "hea": {"value": health, "max": {"useOverride": True, "override": health}},
                "foc": {"value": focus, "max": {"useOverride": True, "override": focus}},
                "inv": {"value": investiture, "max": {"useOverride": True, "override": investiture}},
            },
            "deflect": {"natural": max(0, _int(data.get("deflect"), 0)),
                        "types": {"impact": True, "keen": True, "energy": True,
                                  "spirit": False, "vital": False}},
            "movement": {"walk": _int(data.get("movement_ft"), 0)},
            "senses": {"range": _int(data.get("senses_ft"), 0)},
            "skills": {},
            "origin_world": "scadrial",
        },
        "items": items,
    }


def find_actor_document(catalog, adversary_id):
    """The actor document for ``adversary_id`` from a catalog, or None."""
    if not is_catalog_adversary_id(adversary_id):
        return None
    catalogs = catalog.get("catalogs") or {}
    record = next((row for row in catalogs.get("adversary", [])
                   if isinstance(row, Mapping) and row.get("id") == adversary_id), None)
    if record is None:
        return None
    actions = {row["id"]: row for row in catalogs.get("action", [])
               if isinstance(row, Mapping) and isinstance(row.get("id"), str)}
    return actor_document(record, actions)
