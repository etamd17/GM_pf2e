"""Read-only display projections for Mistborn builds (roster, hub, sheet).

Pure functions over a ``MistbornBuild`` (or the ``CosmereBuild`` facade around
one). They never mutate the build and never consult Stormlight concepts, so a
page can branch on :func:`is_mistborn_build` and keep the Stormlight path as it
was.
"""
from __future__ import annotations

from .lore import FERUCHEMY_STORES, METALS, metal_tint
from .mistborn_build import ALLOMANTIC_PATHS, FERUCHEMICAL_PATHS, MistbornBuild

ERA_LABELS = {"era-1": "Era 1", "era-2": "Era 2"}
PATH_LABELS = {"misting": "Misting", "mistborn": "Mistborn", "feruchemist": "Feruchemist",
               "ferring": "Ferring", "twinborn": "Twinborn"}
POWER_STATE_LABELS = {"nascent": "Nascent", "goal_active": "Goal active",
                      "unlocked": "Unlocked"}
# Mistborn accent (pewter), used where a Stormlight card would use its order colour.
MISTBORN_ACCENT = "#9aa3ad"


def mistborn_build_of(build):
    """The underlying MistbornBuild, or None for any other engine."""
    inner = getattr(build, "_implementation", build)
    return inner if isinstance(inner, MistbornBuild) else None


def is_mistborn_build(build):
    return mistborn_build_of(build) is not None


def _metal_rows(powers, art):
    rows = []
    for metal in sorted(powers):
        info = METALS.get(metal) or {}
        state = powers[metal]
        effect = (FERUCHEMY_STORES.get(metal, "") if art == "feruchemy"
                  else info.get("effect", ""))
        rows.append({"metal": metal, "name": info.get("name") or metal.title(),
                     "effect": effect, "tint": metal_tint(metal),
                     "state": state, "state_label": POWER_STATE_LABELS.get(state, state)})
    return rows


def metalborn_summary(build):
    """Identity facts for a Mistborn build: era, metalborn type and metals."""
    b = mistborn_build_of(build)
    if b is None:
        raise TypeError("metalborn_summary needs a Mistborn build")
    paths = list(b.metalborn_paths())
    label = " / ".join(PATH_LABELS.get(path, path.title()) for path in paths)
    allomancy = _metal_rows(b.allomantic_powers, "allomancy") if set(paths) & ALLOMANTIC_PATHS else []
    feruchemy = _metal_rows(b.feruchemical_powers, "feruchemy") if set(paths) & FERUCHEMICAL_PATHS else []
    metals = [row["name"] for row in allomancy + feruchemy]
    headline = label or "Ordinary hero"
    if metals and len(metals) <= 3:
        headline += " · " + ", ".join(dict.fromkeys(metals))
    return {"era": b.era, "era_label": ERA_LABELS.get(b.era, ""),
            "paths": paths, "label": label, "headline": headline,
            "allomancy": allomancy, "feruchemy": feruchemy,
            "allomancy_rank": b.allomancy_rank, "feruchemy_rank": b.feruchemy_rank,
            "has_allomancy": b.has_allomancy(), "has_feruchemy": b.has_feruchemy()}


def card_identity(build):
    """The roster/hub identity fields for a Mistborn build.

    Mirrors the keys the Stormlight cards carry so shared templates keep
    working; the Radiant fields are empty and ``metalborn`` holds the facts.
    """
    return {"is_radiant": False, "order": "", "spren": "", "surges": [],
            "accent": MISTBORN_ACCENT, "metalborn": metalborn_summary(build)}


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return int(default)


def metallic_arts_panel(build, play_state):
    """The sheet's Allomancy/Feruchemy panel from the build and saved runtime.

    Reads ``play_state['mistborn']`` as the runtime normalizer leaves it; any
    missing value falls back to the build's maximum. Nothing is written back.
    """
    b = mistborn_build_of(build)
    if b is None:
        raise TypeError("metallic_arts_panel needs a Mistborn build")
    summary = metalborn_summary(b)
    state = play_state if isinstance(play_state, dict) else {}
    runtime = state.get("mistborn") if isinstance(state.get("mistborn"), dict) else {}
    allomancy_rt = runtime.get("allomancy") if isinstance(runtime.get("allomancy"), dict) else {}
    feruchemy_rt = runtime.get("feruchemy") if isinstance(runtime.get("feruchemy"), dict) else {}
    burning = {(row.get("parameters") or {}).get("metal")
               for row in allomancy_rt.get("active_burns") or () if isinstance(row, dict)}
    depleted = set(allomancy_rt.get("depleted_metals") or ())
    vials = allomancy_rt.get("vial_inventory") if isinstance(allomancy_rt.get("vial_inventory"), dict) else {}
    vial_counts = {}
    for vial in vials.values():
        if isinstance(vial, dict):
            for metal in vial.get("metals") or ():
                vial_counts[metal] = vial_counts.get(metal, 0) + max(0, _int(vial.get("count"), 0))
    for row in summary["allomancy"]:
        row.update(burning=row["metal"] in burning, depleted=row["metal"] in depleted,
                   vials=vial_counts.get(row["metal"], 0))
    charge_max = b.feruchemy_charge_max() if b.has_feruchemy() else 0
    pools = feruchemy_rt.get("by_metal") if isinstance(feruchemy_rt.get("by_metal"), dict) else {}
    for row in summary["feruchemy"]:
        pool = pools.get(row["metal"]) if isinstance(pools.get(row["metal"]), dict) else {}
        mode = pool.get("mode") if pool.get("mode") in ("storing", "tapping") else "idle"
        row.update(charges=max(0, min(charge_max, _int(pool.get("charge_current"), 0))),
                   charge_max=charge_max, mode=mode)
    investiture_max = b.investiture_max()
    current = allomancy_rt.get("current_investiture", state.get("investiture", investiture_max))
    summary.update(investiture_max=investiture_max,
                   investiture=max(0, min(investiture_max, _int(current, investiture_max))),
                   scaling=b.derived_stats()["metallic_arts"])
    return summary
