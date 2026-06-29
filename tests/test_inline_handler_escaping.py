"""Guard against the apostrophe-injection bug class in inline event handlers.

A value interpolated into an onclick="" attribute's single-quoted JS string must
be JS-escaped (' -> \\'). Two things are NOT enough:
  - raw interpolation         -> "Go'el" closes the string -> SyntaxError, dead button
  - HTML escaping (esc(), &#39;) -> the HTML parser DECODES &#39; back to ' before JS
    runs, so it ALSO breaks (and passes a mangled value if it didn't).
Only .replace(/'/g, "\\'") (optionally chained after esc()) survives. PCs are
routinely named with apostrophes ("Go'el"), and PF2e item/spell/feat/compendium
names too ("Thieves' Tools", "Hunter's Edge"), so every such site is a live bug.

These are static guards (the handlers are built in client JS, no in-repo JS
runner). The fixes were verified live in a browser.
"""
import os
import re

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (file, substring that must appear at the fixed call site) — proves each fix is wired.
_FIXED_SITES = [
    ('templates/player_sheet.html', "useItem('${safeJs}'"),
    ('templates/tracker.html', "approveNomination('${String(m.nominee"),
    ('templates/gmscreen.html', "showCompDetail('${String(r.name"),
    ('templates/player_levelup.html', "removeNewSpell(${idx}, ${diff.lvl}, '${String(s.name"),
    ('templates/player_levelup.html', "initRetrainFeat(${i}, '${String(f.name"),
]

# Templates whose inline handlers must never HTML-escape-without-JS-escaping a value.
_GUARDED = [
    'player_sheet.html', 'tracker.html', 'gmscreen.html', 'player_levelup.html',
    'player_builder.html', 'cosmere_sheet.html', 'cosmere_builder.html',
]

# on<event>="...." attribute values.
_HANDLER = re.compile(r'on[a-z]+="((?:[^"\\]|\\.)*)"')
# a single-quoted JS-string interpolation: '${ ... }'
_SQ_INTERP = re.compile(r"'\$\{([^}]+)\}'")


def _read(rel):
    with open(os.path.join(_REPO, rel), encoding='utf-8') as f:
        return f.read()


def test_fixed_sites_are_js_escaped():
    for rel, needle in _FIXED_SITES:
        assert needle in _read(rel), f"expected JS-escaped call site missing in {rel}: {needle}"


def test_no_html_escape_only_interpolation_in_inline_handlers():
    """An esc(...) inside a single-quoted handler arg is a bug unless it then
    chains .replace(/'  (JS-escape). Flag any that don't."""
    offenders = []
    for name in _GUARDED:
        rel = os.path.join('templates', name)
        text = _read(rel)
        for hm in _HANDLER.finditer(text):
            body = hm.group(1)
            for im in _SQ_INTERP.finditer(body):
                expr = im.group(1)
                # JS-escaped (directly or chained after esc()) -> safe.
                if ".replace(/'" in expr:
                    continue
                # HTML-escape-only inside a JS string -> broken.
                if 'esc(' in expr:
                    offenders.append(f"{name}: '${{{expr}}}'")
    assert not offenders, (
        "HTML-escaped (not JS-escaped) value(s) inside an inline handler's JS string — "
        "apostrophes will break these:\n" + "\n".join(offenders)
    )
