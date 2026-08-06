"""Turn Foundry ``cosmere-rpg`` inline enrichers into readable prose.

The mined content packs store ability / talent / power text with Foundry's
inline enricher markup left in verbatim -- ``[[damage 1d8 keen average]]``,
``[[lookup @actor.name]]{Actor Name}``, ``[[test skill=agi dc=16]]``,
``@UUID[...]{Label}``. Rendered raw it reads like JSON, not English. This module
is the single place that normalises it, shared by the adversary/PC actor
(``actor.py``) and the talent summaries (``radiant_talents.py``) so every
Cosmere surface reads the same way. Depends only on ``re`` -- no cosmere imports,
so it can't create an import cycle.
"""
from __future__ import annotations

import re

# Skill code -> display name, for [[test skill=agi dc=16]] enrichers.
_SKILL_DISPLAY = {
    'agi': 'Agility', 'ath': 'Athletics', 'cra': 'Crafting', 'dec': 'Deception',
    'ded': 'Deduction', 'dis': 'Discipline', 'hwp': 'Heavy Weapon',
    'inm': 'Intimidation', 'ins': 'Insight', 'lea': 'Leadership', 'lor': 'Lore',
    'lwp': 'Light Weapon', 'med': 'Medicine', 'prc': 'Perception',
    'prs': 'Persuasion', 'stl': 'Stealth', 'sur': 'Survival', 'thv': 'Thievery',
    'abr': 'Abrasion', 'adh': 'Adhesion', 'chs': 'Cohesion', 'dvs': 'Division',
    'grv': 'Gravitation', 'ill': 'Illumination', 'prg': 'Progression',
    'trp': 'Transportation', 'trs': 'Transformation', 'tsn': 'Tension',
}
_DAMAGE_TYPES = {'impact', 'keen', 'spirit', 'vital', 'energy', 'healing', 'cognitive'}


def enrich(text, actor_name='') -> str:
    """Turn Foundry ``cosmere-rpg`` inline enrichers into readable prose:

      ``[[damage 1d8 Keen average]]``       -> ``1d8 Keen``
      ``[[damage 2d10 + 9]]``               -> ``2d10 + 9``
      ``[[test skill=agi dc=16]]``          -> ``Agility test (DC 16)``
      ``[[lookup @actor.name]]{Actor Name}``-> the actor's own name
      ``@UUID[...]{Insightful Defense}``     -> ``Insightful Defense``
    """
    if not text:
        return text
    who = actor_name or 'the creature'

    # [[lookup @actor.name]] (optionally with a {fallback}) -> the actor's name.
    text = re.sub(r'\[\[\s*lookup\s+@actor\.name\s*\]\](?:\{[^}]*\})?', who, text, flags=re.I)
    # Any other [[lookup ...]]{Label} -> Label; a bare [[lookup ...]] -> drop.
    text = re.sub(r'\[\[\s*lookup[^\]]*\]\]\{([^}]*)\}', r'\1', text, flags=re.I)
    text = re.sub(r'\[\[\s*lookup[^\]]*\]\]', '', text, flags=re.I)

    # [[test|check skill=xx dc=nn]] -> "<Skill> test (DC nn)".
    def _test(m):
        body = m.group(1)
        sk = re.search(r'skill\s*=\s*([a-z]+)', body, re.I)
        dc = re.search(r'dc\s*=\s*(\d+)', body, re.I)
        name = _SKILL_DISPLAY.get(sk.group(1).lower(), sk.group(1).title()) if sk else 'skill'
        return '%s test (DC %s)' % (name, dc.group(1)) if dc else '%s test' % name
    text = re.sub(r'\[\[\s*(?:/[a-z]+\s+)?(?:test|check)\s+([^\]]*)\]\](?:\{[^}]*\})?',
                  _test, text, flags=re.I)

    # [[damage <formula> [Type] [average]]] -> "<formula> [Type]" (drop "average").
    def _dmg(m):
        inner = re.sub(r'\baverage\b', '', m.group(1), flags=re.I)
        inner = re.sub(r'\s+', ' ', inner).strip()
        parts = inner.rsplit(' ', 1)
        if len(parts) == 2 and parts[1].lower() in _DAMAGE_TYPES:
            inner = '%s %s' % (parts[0], parts[1].capitalize())
        return inner
    text = re.sub(r'\[\[\s*(?:/[a-z]+\s+)?damage\s+([^\]]*)\]\](?:\{[^}]*\})?',
                  _dmg, text, flags=re.I)

    # Foundry content links: @UUID[...]{Label} / @Compendium[...]{Label} -> Label.
    text = re.sub(r'@\w+\[[^\]]*\]\{([^}]*)\}', r'\1', text)
    text = re.sub(r'@\w+\[[^\]]*\]', '', text)

    # Any residual [[...]]{Label} -> Label; a bare [[verb args]] -> args.
    text = re.sub(r'\[\[[^\]]*\]\]\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\[\[\s*(?:/?[a-z]+\s+)?([^\]]*?)\s*\]\]',
                  lambda m: re.sub(r'\s+', ' ', re.sub(r'\baverage\b', '', m.group(1), flags=re.I)).strip(),
                  text)

    # Tidy whitespace/punctuation left by the removals.
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = re.sub(r'\s+([;,.])', r'\1', text)   # "word ;" -> "word;"
    text = re.sub(r';(?=[A-Za-z])', '; ', text)   # ";Hit" -> "; Hit"
    text = re.sub(r'\.(?=[A-Z])', '. ', text)   # "success.If" -> "success. If" (sentence boundary only)
    return text.strip()
