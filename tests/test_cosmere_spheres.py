"""Exact Roshar sphere values and versioned wallet normalization."""

from copy import deepcopy

import pytest

import systems.cosmere.generator as generator
from systems.cosmere.spheres import (
    DENOMINATIONS,
    GEMS,
    format_marks,
    normalize_wallet,
    sphere_value,
    value_in_chips,
    wallet_total,
)


@pytest.mark.parametrize(
    ("gem", "chip", "mark", "broam"),
    (
        ("diamond", 1, 5, 20),
        ("garnet", 5, 25, 100),
        ("heliodor", 5, 25, 100),
        ("topaz", 5, 25, 100),
        ("ruby", 10, 50, 200),
        ("smokestone", 10, 50, 200),
        ("zircon", 10, 50, 200),
        ("amethyst", 25, 125, 500),
        ("sapphire", 25, 125, 500),
        ("emerald", 50, 250, 1000),
    ),
)
def test_every_rulebook_sphere_value_is_exact(gem, chip, mark, broam):
    assert value_in_chips(gem, "chip") == chip
    assert value_in_chips(gem, "mark") == mark
    assert value_in_chips(gem, "broam") == broam


def test_gems_and_denominations_cover_the_rulebook_table():
    assert GEMS == (
        "diamond", "garnet", "heliodor", "topaz", "ruby",
        "smokestone", "zircon", "amethyst", "sapphire", "emerald",
    )
    assert DENOMINATIONS == ("chip", "mark", "broam")


def test_legacy_wallet_becomes_diamond_v2_without_mutating_the_source():
    source = {
        "spheres": {"chip": 3, "mark": 2, "broam": 1},
        "items": [
            {"name": "Sphere pouch", "qty": 1,
             "details": {"colors": ["blue", "white"]}},
        ],
    }
    untouched = deepcopy(source)

    normalized = normalize_wallet(source)

    assert normalized == {
        "version": 2,
        "spheres": {
            "diamond": {"chip": 3, "mark": 2, "broam": 1},
        },
        "items": source["items"],
    }
    assert source == untouched
    normalized["spheres"]["diamond"]["chip"] = 99
    normalized["items"][0]["details"]["colors"].append("green")
    assert source == untouched


def test_normalization_is_idempotent_and_omits_zero_only_gem_groups():
    source = {
        "version": 2,
        "spheres": {
            "diamond": {"chip": 0, "mark": 0, "broam": 0},
            "emerald": {"chip": 0, "mark": 0, "broam": 1},
            "sapphire": {"mark": 2},
        },
        "items": [{"name": "Rope", "qty": 2}],
    }

    once = normalize_wallet(source)
    twice = normalize_wallet(once)

    assert once == twice == {
        "version": 2,
        "spheres": {
            "emerald": {"chip": 0, "mark": 0, "broam": 1},
            "sapphire": {"chip": 0, "mark": 2, "broam": 0},
        },
        "items": [{"name": "Rope", "qty": 2}],
    }


def test_empty_legacy_wallet_keeps_items_but_has_no_zero_only_diamond_group():
    assert normalize_wallet({"items": [{"name": "Map", "qty": 1}]}) == {
        "version": 2,
        "spheres": {},
        "items": [{"name": "Map", "qty": 1}],
    }


def test_stash_and_wallet_totals_stay_exact_in_integer_chips():
    wallet = {
        "version": 2,
        "spheres": {
            "diamond": {"chip": 1, "mark": 0, "broam": 0},
            "garnet": {"chip": 0, "mark": 1, "broam": 0},
            "ruby": {"chip": 0, "mark": 0, "broam": 1},
            "amethyst": {"chip": 2, "mark": 0, "broam": 0},
            "emerald": {"chip": 1, "mark": 0, "broam": 0},
        },
        "items": [],
    }

    assert sphere_value({"chip": 1, "mark": 1, "broam": 1}, "ruby") == 260
    assert wallet_total(wallet) == 326
    assert wallet_total({"spheres": {"chip": 1, "mark": 1, "broam": 1}}) == 26


@pytest.mark.parametrize(
    ("chips", "expected"),
    ((0, "0"), (1, "0.2"), (4, "0.8"), (5, "1"),
     (6, "1.2"), (326, "65.2")),
)
def test_mark_formatter_has_no_float_drift_or_trailing_zero(chips, expected):
    assert format_marks(chips) == expected


def test_stormlight_sphere_generator_uses_all_gems_and_exact_values(monkeypatch):
    choices = iter(("emerald", "broam", "diamond", "chip"))
    quantities = iter((2, 1, 1))

    def choose(options):
        chosen = next(choices)
        if chosen in GEMS:
            assert tuple(options) == GEMS
        else:
            assert tuple(options) == DENOMINATIONS
        return chosen

    monkeypatch.setattr(generator.random, "choice", choose)
    monkeypatch.setattr(generator.random, "randint", lambda _low, _high: next(quantities))

    result = generator.gen_spheres()

    assert "1 emerald broam" in result
    assert "1 diamond chip" in result
    assert "Roughly 200.2 diamond marks’ worth" in result

