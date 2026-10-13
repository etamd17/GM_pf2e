"""Server-side dice for D&D 5e (initiative, death saves, HP rolls, recharge).

Accepts the expressions that appear in 5etools data and on sheets:
``1d20+5``, ``2d6 + 3``, ``d8``, ``4d6kh3`` (keep highest), ``1d20-1``.
Pass an ``rng`` (random.Random) for deterministic tests.
"""
from __future__ import annotations

import random
import re

_SYS = random.SystemRandom()
_TERM = re.compile(r'([+-]?)\s*(?:(\d*)d(\d+)(?:(kh|kl)(\d+))?|(\d+))', re.I)


def roll(expr: str, rng=None) -> dict:
    """Roll ``expr``; returns {'expr', 'total', 'rolls': [[faces, value, kept], ...]}."""
    r = rng or _SYS
    s = (expr or '').replace(' ', '').replace('×', '*')
    if not s or not re.fullmatch(r'[+-]?(\d*d\d+((kh|kl)\d+)?|\d+)([+-](\d*d\d+((kh|kl)\d+)?|\d+))*', s, re.I):
        raise ValueError(f'bad dice expression {expr!r}')
    total = 0
    detail = []
    for m in _TERM.finditer(s):
        sign = -1 if m.group(1) == '-' else 1
        if m.group(3):
            n = int(m.group(2) or 1)
            faces = int(m.group(3))
            if n > 100 or faces > 1000:
                raise ValueError('dice expression too large')
            vals = [r.randint(1, faces) for _ in range(n)]
            keep = list(range(n))
            if m.group(4):
                k = min(n, int(m.group(5)))
                order = sorted(range(n), key=lambda i: vals[i], reverse=m.group(4).lower() == 'kh')
                keep = order[:k]
            sub = sum(vals[i] for i in keep)
            total += sign * sub
            detail += [[faces, v, i in keep] for i, v in enumerate(vals)]
        elif m.group(6):
            total += sign * int(m.group(6))
    return {'expr': expr, 'total': total, 'rolls': detail}


def d20(bonus: int = 0, mode: str = 'normal', rng=None) -> dict:
    """A d20 test with advantage/disadvantage. ``natural`` is the kept die."""
    r = rng or _SYS
    a = r.randint(1, 20)
    b = r.randint(1, 20) if mode in ('adv', 'dis') else None
    nat = a if b is None else (max(a, b) if mode == 'adv' else min(a, b))
    return {'natural': nat, 'dice': [a] + ([b] if b is not None else []), 'bonus': bonus,
            'total': nat + bonus, 'mode': mode}


def average(expr: str) -> int:
    """Average of a dice expression, rounded down (the statblock convention)."""
    s = (expr or '').replace(' ', '')
    total = 0.0
    for m in _TERM.finditer(s):
        sign = -1 if m.group(1) == '-' else 1
        if m.group(3):
            total += sign * int(m.group(2) or 1) * (int(m.group(3)) + 1) / 2
        elif m.group(6):
            total += sign * int(m.group(6))
    return int(total)
