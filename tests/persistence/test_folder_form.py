"""PE-14e: ``folder_form`` is an exact port of the plugin's ``safeSegment``.

The expected values below were generated once by running ``safeSegment``
from ``tools/obsidian-plugin/main.js`` under node over these inputs (the
command is in the PE-14e report), so CI needs no node.
"""

from __future__ import annotations

import pytest

from core.commands.folder_form import JS_WHITESPACE, folder_form, folder_key

# (input, safeSegment(input)) as node printed them.
GOLDEN = [
    ("", "unknown"),
    (None, "unknown"),
    ("a\\b", "a-b"), ("a/b", "a-b"), ("a:b", "a-b"), ("a*b", "a-b"), ("a?b", "a-b"),
    ('a"b', "a-b"), ("a<b", "a-b"), ("a>b", "a-b"), ("a|b", "a-b"), ("a#b", "a-b"),
    ("a^b", "a-b"), ("a[b", "a-b"), ("a]b", "a-b"),
    ('\\/:*?"<>|#^[]', "-------------"),
    ("s:1.2", "s-1.2"),  # ':' is rewritten, '.' is not
    (" session-1", "session-1"),  # a leading space is trimmed
    ("a \t\n b\u00a0\u3000c\r\n", "a b c"),  # mixed whitespace runs collapse
    ("a\u000b\u000cb\u1680\u2000\u200a\u2028\u2029\u202f\u205fc", "a b c"),
    ("\ufeff\ufeffa\ufeff\ufeffb\ufeff", "a b"),  # U+FEFF is JS whitespace
    ("a\u001f\u001fb", "a\u001f\u001fb"),  # U+001F is kept, not collapsed
    ("a\u001c\u001d\u001e b", "a\u001c\u001d\u001e b"),
    ("a\u0085b", "a\u0085b"),  # U+0085 is not JS whitespace either
    ("a\u200bb", "a\u200bb"),  # nor is U+200B
    ("   ", "unknown"),
    ("x" * 120, "x" * 100),  # a 120-character legacy id is cut to 100
    ("a" * 99 + " b", "a" * 99 + " "),  # no trim after the cut
    ("Session-2026-11-08-AB12CD34", "Session-2026-11-08-AB12CD34"),
    ("session-2026-11-08-ab12cd34", "session-2026-11-08-ab12cd34"),
]

# An astral character straddling unit 100: node printed "x" * 99 + "\ud83d" (a
# lone high surrogate).  The port drops that surrogate, a recorded difference.
ASTRAL_INPUT = "x" * 99 + "\U0001F409" + "tail"
ASTRAL_JS = "x" * 99 + "\ud83d"


@pytest.mark.parametrize("value, expected", GOLDEN)
def test_folder_form_matches_safe_segment(value, expected):
    assert folder_form(value) == expected


def test_astral_character_straddling_unit_100_drops_the_lone_surrogate():
    assert folder_form(ASTRAL_INPUT) == ASTRAL_JS[:-1] == "x" * 99


def test_lone_surrogate_in_the_input_is_dropped_too():
    # node keeps it ("a\ud800b"); no stored session id can hold one (review 1, R1-3).
    assert folder_form("a\ud800b") == "ab"


def test_js_whitespace_set_is_the_literal_set():
    expected = {0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0xA0, 0x1680, *range(0x2000, 0x200B),
                0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF}
    assert {ord(c) for c in JS_WHITESPACE} == expected


def test_a_new_form_id_is_its_own_folder_form():
    for value in ("abcdefgh", "s" * 100, "0-session_2026-11-08"):
        assert folder_form(value) == value


# F4 boundary cases (C:613-617): which pairs collide.
@pytest.mark.parametrize("a, b, collide", [
    ("a:b-session", "a-b-session", True),  # legacy ':' against the new form
    ("a.b-session", "a-b-session", False),  # '.' is not rewritten
    ("Session-One", "session-one", True),  # case only
    (" session-one", "session-one", True),  # a leading space is trimmed
    ("s" * 101, "s" * 100, True),  # over 100 characters: its 100-unit prefix
    ("s" * 120 + "x", "s" * 100, True),
    ("s" * 99 + "x", "s" * 100, False),
])
def test_f4_collisions(a, b, collide):
    assert (folder_key(a) == folder_key(b)) is collide
