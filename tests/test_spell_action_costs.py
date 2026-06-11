"""Spell action-cost fidelity — vs the Foundry-sourced master spell list.

Action cost used to come from a hand-maintained ~240-key dict in
``class_matrix.py``; an audit (2026-06-11) found it covered only ~150 of the
1,795 real spells (so 92% rendered blank), carried 25 wrong costs, and keyed on
legacy pre-remaster names the DB no longer uses. The fix drives
``get_action_cost`` from ``compendium_data/spells/master_spells.json`` (the same
Foundry data the spell DB is built from), which has an authoritative ``actions``
value for every spell.

These tests pin that contract: every master spell resolves to a non-empty cost,
action-count spells map to the right glyph, the specific costs the audit flagged
as wrong are now correct, and spells whose casting time is a *duration* (Alarm,
Wish, Teleport) render as compact text rather than a bogus action glyph. The
non-spell combat actions (Strike, Shield Block) the sheet's other tabs rely on
must keep resolving too.
"""
from __future__ import annotations

import json
from pathlib import Path

import class_matrix as cm

_MASTER = json.loads(
    (Path(__file__).resolve().parent.parent
     / "compendium_data" / "spells" / "master_spells.json").read_text(encoding="utf-8")
)

# Foundry encodes action *count* casting times as these strings; everything else
# ("10 minutes", "1 day", ...) is a duration, not an action economy cost.
_GLYPH = {
    "1": "◆", "2": "◆◆", "3": "◆◆◆",
    "reaction": "⟳", "free": "◇",
    "1 to 3": "◆-◆◆◆", "1 or 2": "◆-◆◆", "2 or 3": "◆◆-◆◆◆",
}


def _truth_glyph(actions) -> str | None:
    return _GLYPH.get(str(actions).strip().lower())


def test_every_master_spell_has_an_action_cost():
    """No real spell should render a blank action cost (the 92%-blank bug)."""
    missing = [s["name"] for s in _MASTER if not cm.get_action_cost(s["name"])]
    assert not missing, f"{len(missing)} spells with no action cost, e.g. {missing[:10]}"


def test_action_count_spells_map_to_correct_glyph():
    """Every spell whose casting time is an action count shows the right glyph."""
    wrong = []
    for s in _MASTER:
        g = _truth_glyph(s["actions"])
        if g is not None and cm.get_action_cost(s["name"]) != g:
            wrong.append((s["name"], cm.get_action_cost(s["name"]), g))
    assert not wrong, f"{len(wrong)} wrong glyphs, e.g. {wrong[:10]}"


def test_previously_wrong_costs_are_now_correct():
    """The specific mismatches the audit surfaced."""
    cases = {
        "stinking cloud": "◆◆", "reverse gravity": "◆◆◆", "runic weapon": "◆◆",
        "elemental toss": "◆", "soul siphon": "◆", "tempest touch": "◆",
        "lifelink surge": "◆", "magic hide": "◆", "warp step": "◆◆",
        "sacred form": "◆◆", "spirit veil": "◆◆", "soothing ballad": "◆◆",
        "nymph's token": "◆◆", "eidolon's wrath": "◆◆",
        "fortissimo composition": "◇",
        "faerie dust": "◆-◆◆◆", "heal animal": "◆-◆◆", "heal companion": "◆-◆◆",
    }
    for name, exp in cases.items():
        assert cm.get_action_cost(name) == exp, \
            f"{name}: got {cm.get_action_cost(name)!r}, expected {exp!r}"


def test_duration_spells_render_as_text_not_action_glyphs():
    """Spells cast over minutes/days must not show ◆/⟳ glyphs (design choice:
    show the duration as compact text)."""
    for name in ["alarm", "wish", "teleport", "water breathing", "restoration", "raise dead"]:
        v = cm.get_action_cost(name)
        assert v, f"{name} has no cost"
        assert "◆" not in v and "⟳" not in v and "◇" not in v, f"{name}: {v!r} is a glyph"
    assert cm.get_action_cost("alarm") == "10 min"
    assert cm.get_action_cost("wish") == "1 day"
    assert cm.get_action_cost("teleport") == "10 min"
    assert cm.get_action_cost("restoration") == "1 min"


def test_non_spell_combat_actions_still_resolve():
    """Strike / Shield Block etc. are not in the spell list but the sheet's
    combat tab still expects glyphs for them — the legacy entries must survive."""
    assert cm.get_action_cost("Strike") == "◆"
    assert cm.get_action_cost("Stride") == "◆"
    assert cm.get_action_cost("Shield Block") == "⟳"


def test_unknown_name_returns_empty_string():
    assert cm.get_action_cost("Definitely Not A Real Spell") == ""


def test_lookup_is_case_insensitive():
    assert cm.get_action_cost("FIREBALL") == cm.get_action_cost("fireball") == "◆◆"


# --- spell catalog quality --------------------------------------------------
# The spell DB was built by ingesting Foundry rows, which swept in folder-label
# rows ("Cantrip", "Rank 1".."Rank 10", "Rituals", "Spells", "Focus") as fake
# spells with empty descriptions — they showed up as garbage in spell pickers.
def test_spell_catalog_excludes_foundry_folder_labels():
    import app
    junk = {"cantrip", "focus", "rituals", "spells"} | {f"rank {n}" for n in range(1, 11)}
    leaked = [s["name"] for s in app.BUILDER_SPELLS if s["name"].strip().lower() in junk]
    assert not leaked, f"folder-label rows leaked into the spell catalog: {leaked}"


# Three real rituals ship with empty descriptions in the Foundry data; their
# prose is backfilled from Archives of Nethys (the official SRD) so the sheet
# isn't blank for them.
def test_ap_ritual_descriptions_are_backfilled():
    import app
    by = {s["name"]: s for s in app.BUILDER_SPELLS}
    for n in ["Mindscape Shift", "Open the Wall of Ghosts", "Transmigrate"]:
        assert n in by, f"{n} missing from the spell catalog"
        assert len(by[n].get("description", "").strip()) > 80, \
            f"{n} still has an empty/stub description"
