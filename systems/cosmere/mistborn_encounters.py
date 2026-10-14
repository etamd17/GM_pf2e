"""Literal Mistborn encounter-threat tables and pure budget projections."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy


class MistbornEncounterError(ValueError):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code = code
        self.status = status


# These are source-table literals.  In particular, the zero cells are rules
# data and must not be reconstructed with a geometric formula.
THREAT_BY_LEVEL_DELTA = {
    -3: {"minion": 4, "rival": 8, "boss": 32},
    -2: {"minion": 2, "rival": 4, "boss": 16},
    -1: {"minion": 1, "rival": 2, "boss": 8},
    0: {"minion": 0.5, "rival": 1, "boss": 4},
    1: {"minion": 0.25, "rival": 0.5, "boss": 2},
    2: {"minion": 0, "rival": 0.25, "boss": 1},
    3: {"minion": 0, "rival": 0, "boss": 0.5},
}
DIFFICULTY_MULTIPLIERS = {"easy": 0.5, "average": 1, "hard": 1.5}
ENCOUNTER_SOURCE = {
    "artifact_id": "mistborn.handbook",
    "printed_pages": [359, 360, 361, 362, 363, 364, 365, 366],
    "pdf_pages": [362, 363, 364, 365, 366, 367, 368, 369],
}


def _error(code, message, status=422):
    raise MistbornEncounterError(code, message, status)


def threat_value(level_delta, role):
    if type(level_delta) is not int or level_delta not in THREAT_BY_LEVEL_DELTA:
        _error("invalid_level_delta", "Mistborn level delta must be from -3 through 3.")
    if role == "ordinary":
        role = "rival"
    if not isinstance(role, str) or role not in THREAT_BY_LEVEL_DELTA[level_delta]:
        _error("invalid_role", "Mistborn adversary role is invalid.")
    return THREAT_BY_LEVEL_DELTA[level_delta][role]


def difficulty_targets(party_size):
    if type(party_size) is not int or party_size < 1:
        _error("invalid_party_size", "Party size must be a positive integer.")
    return {name: factor * party_size
            for name, factor in DIFFICULTY_MULTIPLIERS.items()}


def mistborn_encounter_budget(adversaries, *, party_size):
    """Sum caller-supplied, already-resolved literal delta rows.

    The profile adapter is responsible for deriving each adversary's level
    delta from its authoritative encounter context.  Keeping that derivation
    outside this table lookup prevents an ambiguous delta convention from
    becoming a second rules authority.
    """
    targets = difficulty_targets(party_size)
    if not isinstance(adversaries, Sequence) or isinstance(
            adversaries, (str, bytes, bytearray)):
        _error("invalid_adversaries", "Adversaries must be a list.")
    entries = []
    total = 0
    for raw in adversaries:
        if not isinstance(raw, Mapping):
            _error("invalid_adversary", "Mistborn adversary entry is invalid.")
        count = raw.get("count", 1)
        if type(count) is not int or count < 1:
            _error("invalid_count", "Adversary count must be a positive integer.")
        unit = threat_value(raw.get("level_delta"), raw.get("role"))
        subtotal = unit * count
        total += subtotal
        entries.append({
            "id": raw.get("id"),
            "role": "rival" if raw.get("role") == "ordinary" else raw.get("role"),
            "level_delta": raw.get("level_delta"),
            "count": count,
            "unit_threat": unit,
            "threat": subtotal,
        })
    return {
        "strategy": "mistborn.literal-threat.v1",
        "threat": total,
        "targets": targets,
        "entries": deepcopy(entries),
        "automation_disposition": "calculated",
        "source": deepcopy(ENCOUNTER_SOURCE),
    }


def encounter_budget_for_party(actors, adversaries):
    """Resolve the source table's party-tier-versus-adversary-tier row.

    The handbook table is phrased from the party's perspective: a party one
    tier lower than an adversary uses row ``-1``.  Actor and adversary tiers are
    server projections; callers cannot supply a competing ``level_delta``.
    """
    if not isinstance(actors, Sequence) or isinstance(
            actors, (str, bytes, bytearray)) or not actors:
        _error("invalid_party_size", "Mistborn encounters require at least one character.")
    party_tiers = set()
    for actor in actors:
        if not isinstance(actor, Mapping):
            _error("invalid_party_tier", "Mistborn party member is invalid.")
        tier = actor.get("tier")
        if type(tier) is not int or tier not in {1, 2, 3, 4}:
            _error("invalid_party_tier", "Mistborn party tier must be from 1 through 4.")
        party_tiers.add(tier)
    if len(party_tiers) != 1:
        _error(
            "mixed_party_tier",
            "Mistborn encounter threat requires the party to share one tier.")
    party_tier = next(iter(party_tiers))
    if not isinstance(adversaries, Sequence) or isinstance(
            adversaries, (str, bytes, bytearray)):
        _error("invalid_adversaries", "Adversaries must be a list.")
    resolved = []
    for raw in adversaries:
        if not isinstance(raw, Mapping):
            _error("invalid_adversary", "Mistborn adversary entry is invalid.")
        adversary_tier = raw.get("tier")
        if type(adversary_tier) is not int or adversary_tier not in {1, 2, 3, 4}:
            _error(
                "invalid_adversary_tier",
                "Mistborn adversary tier must be from 1 through 4.")
        row = deepcopy(dict(raw))
        row["level_delta"] = party_tier - adversary_tier
        resolved.append(row)
    result = mistborn_encounter_budget(resolved, party_size=len(actors))
    result["party_tier"] = party_tier
    return result
