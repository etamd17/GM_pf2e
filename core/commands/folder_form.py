r"""The plugin's session folder name, ported exactly from ``safeSegment`` (PE-14e).

``tools/obsidian-plugin/main.js``::

    String(value || 'unknown')
      .replace(/[\\/:*?"<>|#^[\]]/g, '-')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 100) || 'unknown'

JS whitespace (``\s`` and ``trim``) is the literal set below.  Python's
``\s`` and ``str.strip()`` are not used: they also match U+001C-U+001F and
U+0085 and miss U+FEFF.  The cut is 100 UTF-16 code units.  Where an astral
character straddles unit 100, JS keeps a lone high surrogate and this port
drops it (a lone surrogate is not a usable folder name).

Two ids collide when their folder forms are equal case-folded
(:func:`folder_key`).  A new-form id (``[a-z0-9][a-z0-9_-]{7,99}``) is its own
folder form (C:484).
"""

from __future__ import annotations

_REWRITTEN = frozenset('\\/:*?"<>|#^[]')
JS_WHITESPACE = ("\t\n\v\f\r \u00a0\u1680"
                 + "".join(chr(c) for c in range(0x2000, 0x200B))
                 + "\u2028\u2029\u202f\u205f\u3000\ufeff")
_WHITESPACE = frozenset(JS_WHITESPACE)
MAX_UNITS = 100


def folder_form(value: str | None) -> str:
    """``safeSegment(value)`` for a string or ``None``."""

    out: list[str] = []
    in_run = False  # each run of JS whitespace becomes one space
    for ch in value or "unknown":
        if ch in _WHITESPACE:
            if not in_run:
                out.append(" ")
            in_run = True
        else:
            out.append("-" if ch in _REWRITTEN else ch)
            in_run = False
    text = "".join(out).strip(JS_WHITESPACE)
    units = text.encode("utf-16-le", "surrogatepass")[:2 * MAX_UNITS]
    return units.decode("utf-16-le", "ignore") or "unknown"


def folder_key(value: str | None) -> str:
    """The collision key: the folder form, case-folded."""

    return folder_form(value).casefold()
