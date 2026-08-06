"""Snapshot test helper.

Picks a stable, diff-friendly subset of a Character's computed sheet and
either:
  * writes it to `tests/snapshots/<key>.json` on first run (then skips), or
  * compares against the saved snapshot on subsequent runs.

Why an explicit allowlist instead of `Character.as_dict()`:
  - `as_dict()` deepcopies `_build_ref`, so the snapshot would just be the
    raw input echoed back — no signal on the actual computation.
  - It includes runtime-only fields (`instance_id`, `file_path`,
    `session_notes`, `expended_slots`, etc.) that aren't part of the
    "what the rules engine produces from this PC" contract we care about.

Updating snapshots: delete the file under `tests/snapshots/` and re-run
pytest. The harness regenerates it. CI failures should investigate why
the value changed before regenerating.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

_SNAP_DIR = Path(__file__).resolve().parent / "snapshots"


def _normalize_feats(feats):
    """Reduce feats to {name, level, type} only, sorted by (level, name).
    Drops the rendered HTML description so a compendium tweak that touches
    feat copy doesn't cascade into snapshot churn."""
    if not isinstance(feats, list):
        return []
    out = []
    for f in feats:
        if not isinstance(f, dict):
            continue
        out.append({
            "name": f.get("name", ""),
            "level": f.get("level", 0),
            "type": f.get("type", ""),
        })
    out.sort(key=lambda x: (x.get("level", 0), x.get("name", "")))
    return out


def _normalize_proficiencies(profs):
    """Sort the proficiencies dict by key for stable diffs. Values
    are PF2e ranks (0/2/4/6/8)."""
    if not isinstance(profs, dict):
        return {}
    return {k: profs[k] for k in sorted(profs.keys())}


def _normalize_rule_modifiers(rms):
    """Same idea: deep-sort so two equivalent runs produce identical JSON."""
    if not isinstance(rms, dict):
        return {}
    out = {}
    for sel in sorted(rms.keys()):
        bucket = rms[sel] or {}
        if isinstance(bucket, dict):
            out[sel] = {
                btype: sorted(bucket[btype]) if isinstance(bucket.get(btype), list) else bucket.get(btype)
                for btype in sorted(bucket.keys())
            }
        else:
            out[sel] = bucket
    return out


def _normalize_attacks(attacks):
    """Each attack dict has a `strikes` list of {label, mod, map, map_label}
    representing the three iterations of the action economy (no MAP / -5 /
    -10). We snapshot the first-strike mod (the meaningful number) and the
    damage formula. The full MAP cascade is deterministic from the first
    mod, so capturing it would be redundant noise."""
    if not isinstance(attacks, list):
        return []
    out = []
    for a in attacks:
        if not isinstance(a, dict):
            continue
        strikes = a.get("strikes") or []
        first = strikes[0] if strikes else {}
        out.append({
            "name": a.get("name", ""),
            "first_strike": first.get("mod"),
            "damage": a.get("damage", ""),
            "traits": sorted(a.get("traits", [])) if isinstance(a.get("traits"), list) else a.get("traits"),
        })
    out.sort(key=lambda x: x.get("name", ""))
    return out


def _normalize_skills(skills):
    """`Character.skills` is a list of dicts: each entry has name, stat,
    prof_val, prof_letter, total, penalty, breakdown. Drop the human-readable
    `breakdown` (its formatting can change without the underlying number
    changing) and the redundant `prof_letter`/`prof_val` (proficiencies
    dict already covers ranks). Keep name + total + penalty so a +1 swing
    in a single skill bonus is visible."""
    if not isinstance(skills, list):
        return []
    out = []
    for s in skills:
        if isinstance(s, dict):
            out.append({
                "name": s.get("name", ""),
                "stat": s.get("stat", ""),
                "total": s.get("total", ""),
                "penalty": s.get("penalty", 0),
            })
    out.sort(key=lambda x: x.get("name", ""))
    return out


def serialize_character(c) -> dict[str, Any]:
    """Return the diff-friendly snapshot view of a Character. Keep this
    function in sync with what the rules engine is expected to compute —
    every field here is "the engine should produce this exact value
    given this PC's Pathbuilder JSON." Adding a new field is fine; just
    re-snapshot afterward."""
    snap: dict[str, Any] = {
        # Identity
        "name": c.name,
        "level": c.level,
        "class": c.class_name,
        "subclass": c.subclass,
        "ancestry": c.ancestry,
        "heritage": c.heritage,
        "background": c.background,
        "size": c.size,
        "deity": getattr(c, "deity", ""),
        "languages": sorted(c.languages or []),

        # Resources
        "hp": c.hp,
        "current_hp": getattr(c, "current_hp", c.hp),
        "focus_max": c.focus_max,
        "hero_points": c.hero_points,

        # Core sheet numbers (these are properties / computed)
        "ac": c.ac,
        "fort": c.fort,
        "ref": c.ref,
        "will": c.will,
        "perception": c.perception,
        "speed": c.speed,
        "class_dc": c.class_dc,
        "spell_attack": c.spell_attack,
        "spell_dc": c.spell_dc,
        "initiative_mod": c.initiative_mod,

        # Ability modifiers
        "mods": dict(sorted((c.mods or {}).items())),

        # Proficiency table — the rules-engine OUTPUT, after PB import
        # + class progression + feats + ActiveEffectLike rules.
        "proficiencies": _normalize_proficiencies(c.proficiencies),

        # Skill totals (computed). `Character.skills` is a list of dicts.
        "skills": _normalize_skills(c.skills),

        # Senses / immunities
        "senses": sorted(c.senses or []),
        "immunities": sorted(c.immunities or []),

        # Armor + shield
        "armor_name": getattr(c, "armor_name", ""),
        "ac_item": getattr(c, "ac_item", 0),
        "armor_str_req": getattr(c, "armor_str_req", 0),
        "active_armor_penalty": getattr(c, "active_armor_penalty", 0),
        "active_speed_penalty": getattr(c, "active_speed_penalty", 0),
        "stealth_penalty": getattr(c, "stealth_penalty", 0),
        "shield_ac_bonus": getattr(c, "shield_ac_bonus", 0),
        "shield_max_hp": getattr(c, "shield_max_hp", 0),
        "shield_hardness": getattr(c, "shield_hardness", 0),
        "shield_bt": getattr(c, "shield_bt", 0),

        # Feats (name+level only)
        "feats": _normalize_feats(getattr(c, "feats", [])),

        # Strikes / weapon attacks (computed)
        "attacks": _normalize_attacks(getattr(c, "attacks", [])),

        # Rule-engine output: every typed bonus the engine accumulated.
        # This is the most regression-prone field — any feat parsing
        # change shows up here.
        "rule_modifiers": _normalize_rule_modifiers(c.rule_modifiers),
    }
    return snap


def assert_matches_snapshot(key: str, payload: dict[str, Any]) -> None:
    """First call for a given key writes the snapshot and skips the test
    with a CREATED message. Subsequent calls assert equality.

    Mismatch behavior: writes the new candidate to `<key>.json.new`
    next to the saved snapshot so a `diff` can show what changed.
    The test then fails with a pointer to that file."""
    actual_text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    path = _SNAP_DIR / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        path.write_text(actual_text + "\n", encoding="utf-8")
        pytest.skip(f"created snapshot {path.name} (re-run to verify)")

    expected_text = path.read_text(encoding="utf-8").rstrip("\n")
    if actual_text != expected_text:
        new_path = path.with_suffix(".json.new")
        new_path.write_text(actual_text + "\n", encoding="utf-8")
        pytest.fail(
            f"snapshot mismatch for {key}. "
            f"Wrote candidate to {new_path.relative_to(_SNAP_DIR.parent.parent)}. "
            f"Diff with the saved snapshot, then either fix the regression or "
            f"replace {path.name} to accept the change."
        )
