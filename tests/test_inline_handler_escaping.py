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
import glob

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


# Server-rendered variant: a Jinja value passed as a single-quoted call argument
# in an inline handler, e.g. onclick="toggleFeature('{{ feat.name }}')". Raw
# {{ x }} autoescapes ' -> &#39, which the HTML parser decodes back to ' before
# the JS runs -> the string closes early -> dead button. (|e is HTML-escape, same
# trap.) Only |replace("'", "\\'") (JS-escape) survives. Names/titles/labels are
# the apostrophe-prone fields ("Hunter's Edge", "Go'el", "Thieves' Tools").
_HANDLER_ON_LINE = re.compile(r'\bon[a-z]+\s*=\s*"')
# a Jinja interpolation used as a call argument: ( or , then '{{ EXPR }}TRAILING'
# up to the quote that actually closes the JS arg (next char is , or)).
# NB: a naive r"[(,]\s*'\{\{\s*(.*?)\s*\}\}'" is too greedy across multiple
# interpolations on the same line — when a LATER argument's expr contains its
# own literal quotes (e.g. a Jinja |replace("'", "\\'") filter chain), the lazy
# .*? backtracks past the first argument's closing quote and swallows both
# arguments into one match. If that combined match happens to contain a
# "safe" substring like "replace", the guard below silently skips a line that
# actually has an unescaped EARLIER argument. Anchoring each match to ITS OWN
# closing quote (lookahead for the following , or)) keeps interpolations
# independent so an unescaped one adjacent to an escaped one is still caught.
_JINJA_CALL_ARG = re.compile(r"[(,]\s*'\{\{\s*(.*?)\s*\}\}([^'\"]*)'(?=[,)])", re.S)
_NAMELIKE = re.compile(r'\b(name|title|label)\b')


def test_no_unescaped_jinja_name_in_inline_handler():
    offenders = []
    for path in glob.glob(os.path.join(_REPO, 'templates', '**', '*.html'), recursive=True):
        with open(path, encoding='utf-8') as f:
            for i, line in enumerate(f, 1):
                if not _HANDLER_ON_LINE.search(line):
                    continue
                for m in _JINJA_CALL_ARG.finditer(line):
                    expr = m.group(1)
                    if not _NAMELIKE.search(expr):
                        continue  # constants (codes, ids, conditions) can't hold apostrophes
                    if 'replace' in expr or 'tojson' in expr:
                        continue  # JS-escaped (replace) or single-quote-safe (tojson)
                    offenders.append('%s:%d  %s' % (os.path.basename(path), i, expr))
    assert not offenders, (
        "Raw/HTML-escaped Jinja name passed to an inline handler — autoescaped "
        "apostrophes (&#39;) decode back to ' and break the JS string. Use "
        "|replace(\"'\", \"\\\\'\"):\n" + "\n".join(offenders)
    )


# Chronicle rule (stricter than escaping): data-* + addEventListener ONLY —
# no inline onclick/onload/on* at all. A leading \s ensures we match real
# attributes and skip data-on-* and mid-word "on".
_INLINE_HANDLER_ANY = re.compile(r'\son[a-z]+\s*=\s*"')


def test_chronicle_templates_have_no_inline_handlers():
    files = glob.glob(os.path.join(_REPO, "templates", "chronicle*.html"))
    assert files, "no chronicle*.html templates found (guard would silently pass)"
    offenders = []
    for path in files:
        for i, line in enumerate(_read(os.path.relpath(path, _REPO)).splitlines(), 1):
            if _INLINE_HANDLER_ANY.search(line):
                offenders.append(f"{os.path.basename(path)}:{i}: {line.strip()}")
    assert not offenders, (
        "inline event handlers in Chronicle templates (use addEventListener):\n"
        + "\n".join(offenders)
    )
