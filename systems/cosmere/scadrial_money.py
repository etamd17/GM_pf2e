"""Scadrial money, kept independently of Roshar spheres.

One boxing is 100 clips in both eras. Source: installed Mistborn core 1.1.0,
mistborn.currency.boxing / mistborn.currency.clip, handbook printed p254
(PDF p257). Banknotes are boxings, not a third denomination.
"""
from collections.abc import Mapping
from copy import deepcopy

CLIPS_PER_BOXING = 100
DENOMINATIONS = ('boxing', 'clip')
# Keep counts and totals exact in browser JSON as well as Python integers.
MAX_COUNT = 10**12


def _count(value):
    if isinstance(value, bool):
        return 0
    try:
        count = int(value)
        if str(value).strip() != str(count):
            return 0
        return max(0, min(MAX_COUNT, count))
    except (ValueError, TypeError, OverflowError):
        return 0


def normalize_money(money):
    source = money if isinstance(money, Mapping) else {}
    return {'version': 1, **{key: _count(source.get(key)) for key in DENOMINATIONS}}


def validate_money(money):
    """Validate writes; never silently turn an invalid award into another amount."""
    if not isinstance(money, dict) or set(money) - {'version', *DENOMINATIONS}:
        raise ValueError('Use boxings and clips only.')
    for key in DENOMINATIONS:
        value = money.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_COUNT:
            raise ValueError('Money counts must be non-negative whole numbers within the limit.')
    return normalize_money(money)


def normalize_wallet(wallet):
    """Detached view; never convert, normalize or discard the sphere stash."""
    result = deepcopy(dict(wallet)) if isinstance(wallet, Mapping) else {}
    result['scadrial'] = normalize_money(result.get('scadrial'))
    if not isinstance(result.get('items'), list):
        result['items'] = []
    return result


def wallet_total(wallet):
    money = normalize_wallet(wallet)['scadrial']
    return money['boxing'] * CLIPS_PER_BOXING + money['clip']


def format_money(clips):
    boxings, remainder = divmod(int(clips), CLIPS_PER_BOXING)
    return (f"{boxings} {'boxing' if boxings == 1 else 'boxings'}, "
            f"{remainder} {'clip' if remainder == 1 else 'clips'}")
