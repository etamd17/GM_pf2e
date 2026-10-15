"""Roshar sphere values and versioned wallet normalization.

Values are kept in diamond chips, the smallest unit in the rules table.  One
diamond mark is five chips, so all supported sphere values remain integers and
totals never pass through floating point arithmetic.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy


GEMS = (
    "diamond",
    "garnet",
    "heliodor",
    "topaz",
    "ruby",
    "smokestone",
    "zircon",
    "amethyst",
    "sapphire",
    "emerald",
)
DENOMINATIONS = ("chip", "mark", "broam")

_VALUE_TIERS = {
    "diamond": (1, 5, 20),
    "garnet": (5, 25, 100),
    "heliodor": (5, 25, 100),
    "topaz": (5, 25, 100),
    "ruby": (10, 50, 200),
    "smokestone": (10, 50, 200),
    "zircon": (10, 50, 200),
    "amethyst": (25, 125, 500),
    "sapphire": (25, 125, 500),
    "emerald": (50, 250, 1000),
}


def value_in_chips(gem, denomination):
    """Return one sphere's exact value in diamond chips."""
    gem_key = str(gem or "").strip().casefold()
    denomination_key = str(denomination or "").strip().casefold()
    try:
        denomination_index = DENOMINATIONS.index(denomination_key)
        return _VALUE_TIERS[gem_key][denomination_index]
    except (KeyError, ValueError) as error:
        raise ValueError(
            f"Unknown sphere gem or denomination: {gem!r} {denomination!r}"
        ) from error


def _count(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def sphere_value(spheres, gem="diamond"):
    """Return a single gem stash's exact value in diamond chips."""
    stash = spheres if isinstance(spheres, Mapping) else {}
    return sum(
        _count(stash.get(denomination)) * value_in_chips(gem, denomination)
        for denomination in DENOMINATIONS
    )


def _complete_stash(stash):
    source = stash if isinstance(stash, Mapping) else {}
    complete = {denomination: _count(source.get(denomination))
                for denomination in DENOMINATIONS}
    return complete if any(complete.values()) else None


def normalize_wallet(wallet):
    """Return a detached canonical v2 wallet without changing the source.

    Flat, unversioned sphere counts are legacy data and therefore represent
    diamond spheres.  Canonical wallets are sparse by gem, while every gem
    group that is present contains all three denominations.
    """
    source = wallet if isinstance(wallet, Mapping) else {}
    source_spheres = source.get("spheres")
    source_spheres = source_spheres if isinstance(source_spheres, Mapping) else {}
    normalized_spheres = {}

    is_v2 = source.get("version") == 2
    if not is_v2:
        diamond = _complete_stash(source_spheres)
        if diamond is not None:
            normalized_spheres["diamond"] = diamond
    else:
        folded = {
            str(gem).strip().casefold(): stash
            for gem, stash in source_spheres.items()
        }
        for gem in GEMS:
            stash = _complete_stash(folded.get(gem))
            if stash is not None:
                normalized_spheres[gem] = stash

    items = source.get("items")
    return {
        "version": 2,
        "spheres": normalized_spheres,
        "items": deepcopy(items) if isinstance(items, list) else [],
    }


def wallet_total(wallet):
    """Return a legacy or v2 wallet's total value in diamond chips."""
    normalized = normalize_wallet(wallet)
    return sum(sphere_value(stash, gem)
               for gem, stash in normalized["spheres"].items())


def format_marks(chips):
    """Format an integer diamond-chip total as an exact numeric mark string."""
    total = int(chips or 0)
    sign = "-" if total < 0 else ""
    whole, remainder = divmod(abs(total), 5)
    amount = str(whole) if remainder == 0 else f"{whole}.{remainder * 2}"
    return f"{sign}{amount}"
