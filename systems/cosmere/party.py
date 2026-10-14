"""System-neutral party capability projection for canonical Cosmere actors."""
from __future__ import annotations

from copy import deepcopy

from systems.cosmere.documents import normalize_character_document
from systems.cosmere import SKILL_NAMES


_DEFENSE_NAMES = {"phy": "physical", "cog": "cognitive", "spi": "spiritual"}


def _resource(runtime, derived, key, maximum_key):
    maximum = max(0, int(derived.get(maximum_key, 0) or 0))
    current = runtime.get(key, maximum)
    try:
        current = max(0, min(maximum, int(current))) if maximum else max(0, int(current or 0))
    except (TypeError, ValueError):
        current = maximum
    return {"current": current, "max": maximum}


def _investiture_resource(runtime, derived):
    """Project the active profile's canonical Investiture pool.

    Stormlight keeps the compatibility value at the runtime root. Mistborn
    owns it per Allomantic state so a stale legacy projection must not win.
    """
    mistborn = runtime.get("mistborn") if isinstance(runtime.get("mistborn"), dict) else {}
    allomancy = mistborn.get("allomancy") if isinstance(mistborn.get("allomancy"), dict) else {}
    if allomancy:
        try:
            maximum = max(0, int(allomancy.get("max_investiture", 0) or 0))
            current = max(0, min(maximum, int(
                allomancy.get("current_investiture", maximum) or 0)))
        except (TypeError, ValueError):
            maximum = max(0, int(derived.get("investiture_max", 0) or 0))
            current = maximum
        return {"current": current, "max": maximum}
    return _resource(runtime, derived, "investiture", "investiture_max")


def project_member(document, *, homebrew=None):
    if not isinstance(document, dict) or not isinstance(document.get("id"), str):
        raise ValueError("Cosmere party member requires a stable identity")
    normalized = normalize_character_document(
        document, actor_id=document["id"],
        binding=document.get("rules_binding"), homebrew=homebrew)
    build = normalized["build"]
    derived = normalized["derived"]
    runtime = normalized["runtime_state"]
    defenses = {name: int((derived.get("defenses") or {}).get(code, 0) or 0)
                for code, name in _DEFENSE_NAMES.items()}
    skills = {}
    for code, rank in (build.get("skills") or {}).items():
        name = SKILL_NAMES.get(code, code).strip().lower().replace(" ", "_")
        try:
            skills[name] = int(rank or 0)
        except (TypeError, ValueError):
            skills[name] = 0
    capabilities = []
    for talent in build.get("talents") or []:
        if isinstance(talent, dict):
            capabilities.append({"id": str(talent.get("id") or talent.get("name") or ""),
                                 "name": str(talent.get("name") or talent.get("id") or "")})
        elif isinstance(talent, str):
            capabilities.append({"id": talent, "name": talent})
    capabilities.sort(key=lambda row: (row["name"].casefold(), row["id"]))
    return {
        "id": normalized["id"], "name": normalized.get("name") or build.get("name") or "",
        "level": derived.get("level"), "tier": derived.get("tier"),
        "world": normalized["origin_world"], "era": normalized.get("era"),
        "defenses": defenses,
        "resources": {
            "health": _resource(runtime, derived, "health", "health_max"),
            "focus": _resource(runtime, derived, "focus", "focus_max"),
            "investiture": _investiture_resource(runtime, derived),
        },
        "mistborn": deepcopy(runtime.get("mistborn") or {}),
        "skills": dict(sorted(skills.items())),
        "capabilities": capabilities,
        "effects": deepcopy([row for row in runtime.get("effects", [])
                             if isinstance(row, dict) and not row.get("expired")]),
    }


def project_party(documents, *, homebrew=None):
    members = [project_member(document, homebrew=homebrew)
               for document in documents]
    members.sort(key=lambda row: (row["name"].casefold(), row["id"]))
    return {"members": members, "count": len(members)}
