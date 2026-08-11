"""Small, deterministic helpers for Session Operations roll results.

Actor modifiers, strike formulas, spell data, and mutations remain in
``app.py``.  This module only normalizes dice output and PF2e degrees so every
Obsidian command returns the same structured shape.
"""
from __future__ import annotations

import random
import re


_DICE_RE = re.compile(
    r"^\s*(?P<count>\d{1,3})d(?P<sides>\d{1,4})"
    r"(?:\s*(?P<sign>[+-])\s*(?P<bonus>\d{1,6}))?",
    re.IGNORECASE,
)


def degree_of_success(total: int, dc: int, d20: int | None = None) -> str:
    if total >= dc + 10:
        step = 3
    elif total >= dc:
        step = 2
    elif total <= dc - 10:
        step = 0
    else:
        step = 1
    if d20 == 20:
        step = min(3, step + 1)
    elif d20 == 1:
        step = max(0, step - 1)
    return ("critical_failure", "failure", "success", "critical_success")[step]


def roll_check(modifier: int, dc: int | None = None, *, d20: int | None = None) -> dict:
    natural = int(d20) if d20 not in (None, "") else random.randint(1, 20)
    natural = max(1, min(20, natural))
    modifier = int(modifier or 0)
    total = natural + modifier
    result = {
        "formula": f"1d20{modifier:+d}",
        "dice": [{"sides": 20, "value": natural}],
        "d20": natural,
        "modifier": modifier,
        "total": total,
        "dc": int(dc) if dc not in (None, "") else None,
        "degree": None,
    }
    if result["dc"] is not None:
        result["degree"] = degree_of_success(total, result["dc"], natural)
    return result


def parse_dice_expression(expression: str) -> tuple[int, int, int]:
    match = _DICE_RE.match(str(expression or ""))
    if not match:
        raise ValueError("unrecognized dice expression")
    count = int(match.group("count"))
    sides = int(match.group("sides"))
    bonus = int(match.group("bonus") or 0)
    if match.group("sign") == "-":
        bonus *= -1
    if count < 1 or count > 100 or sides < 2 or sides > 1000:
        raise ValueError("dice expression is outside safe limits")
    return count, sides, bonus


def roll_damage(expression: str) -> dict:
    count, sides, bonus = parse_dice_expression(expression)
    dice = [random.randint(1, sides) for _ in range(count)]
    total = max(0, sum(dice) + bonus)
    formula = f"{count}d{sides}{bonus:+d}" if bonus else f"{count}d{sides}"
    return {
        "formula": formula,
        "dice": [{"sides": sides, "value": value} for value in dice],
        "modifier": bonus,
        "total": total,
    }


def signed(value: int) -> str:
    value = int(value or 0)
    return f"+{value}" if value >= 0 else str(value)
