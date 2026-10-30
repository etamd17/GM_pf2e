"""Read the design tokens out of static/css/system.css (DS-01).

The site's tokens are CSS custom properties. Two things need their resolved
values outside a browser:

* ``static/css/ds-tokens.css`` -- the account shell and the splash page do not
  load system.css, so they get a generated copy of the design-system tokens
  with the canonical PF2e values (``generate`` writes it, and
  tests/test_ds_tokens.py fails if the file drifts from system.css);
* ``tools/check_contrast.py`` -- WCAG ratios per theme.

Resolution models the two elements that carry tokens: ``<html>`` (``:root``,
``html``, ``[data-theme="dark"]``) and ``<body>`` (``body`` plus the theme
classes). A custom property resolves ``var()`` on the element where it is
declared and is inherited already resolved, exactly as browsers do, which is
why the colour roles are declared on ``:root, body``.

Usage:
    python tools/design_tokens.py generate      # rewrite static/css/ds-tokens.css
    python tools/design_tokens.py check         # exit 1 if it is out of date
    python tools/design_tokens.py show [theme]  # print resolved tokens
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_CSS = ROOT / "static" / "css" / "system.css"
DS_TOKENS_CSS = ROOT / "static" / "css" / "ds-tokens.css"

THEMES = {
    "pf2e": (),
    "stormlight": ("system-cosmere",),
    "mistborn": ("system-cosmere", "cosmere-mistborn"),
    # D&D 5e sets no surface, text or status tokens (dnd5e.css), so its shared
    # controls render the PF2e values until the DS-24a mockup (Q4).
    "dnd5e": (),
}

HTML_SELECTORS = {":root", "html", '[data-theme="dark"]'}

# Names copied into ds-tokens.css: every design-system token plus the existing
# spacing, radius and shadow scale they are built on.
DS_PREFIXES = ("--c-", "--fs-", "--lh-", "--fw-", "--pad-", "--gap-", "--layer-",
               "--dur-", "--ease-", "--motion-", "--sp-", "--shadow-1", "--shadow-2")
DS_NAMES = ("--r-sm", "--r-md", "--r-round")


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def parse(css: str):
    """Return (rules, media_rules) as lists of (selectors, declarations, order).

    Only plain rule blocks are kept; at-rule contents are returned separately
    with their prelude so the media overrides can be mirrored."""
    css = _strip_comments(css)
    rules, media, order = [], [], 0
    i, n = 0, len(css)
    while i < n:
        brace = css.find("{", i)
        if brace < 0:
            break
        prelude = css[i:brace].strip()
        depth, j = 1, brace + 1
        while j < n and depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        body = css[brace + 1:j - 1]
        if prelude.startswith("@"):
            if prelude.startswith("@media"):
                for sels, decls, _ in parse(body)[0]:
                    media.append((prelude, sels, decls, order))
                    order += 1
        else:
            rules.append(([s.strip() for s in prelude.split(",")], _declarations(body), order))
            order += 1
        i = j
    return rules, media


def _declarations(body: str):
    out = []
    for part in _split_top(body, ";"):
        if ":" not in part:
            continue
        name, value = part.split(":", 1)
        name = name.strip()
        if not name.startswith("--"):
            continue
        value = " ".join(value.split())
        important = value.endswith("!important")
        if important:
            value = value[: -len("!important")].strip()
        out.append((name, value, important))
    return out


def _split_top(text: str, sep: str):
    parts, depth, start = [], 0, 0
    for k, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(text[start:k])
            start = k + 1
    parts.append(text[start:])
    return parts


def _body_selector_specificity(selector: str, classes) -> int | None:
    """Specificity rank of a selector that matches <body>, else None."""
    if selector == "body":
        return 1
    m = re.fullmatch(r"body((?:\.[\w-]+)+)", selector)
    if m:
        needed = m.group(1).split(".")[1:]
        if all(c in classes for c in needed):
            return 1 + 10 * len(needed)
    return None


def _cascade(matches):
    """matches: (important, specificity, order, name, value) -> {name: value}."""
    won = {}
    ranked = sorted(enumerate(matches), key=lambda item: (item[1][:3], item[0]))
    for _, (important, spec, order, name, value) in ranked:
        won.pop(name, None)
        won[name] = value
    return won


def declared(css: str, theme: str = "pf2e"):
    """Declared custom properties on <html> and on <body> for a theme."""
    rules, _ = parse(css)
    classes = set(THEMES[theme])
    html, body = [], []
    for sels, decls, order in rules:
        for sel in sels:
            spec = 1 if sel in HTML_SELECTORS else None
            if spec is not None:
                if sel == ":root" or sel.startswith("["):
                    spec = 10
                html += [(imp, spec, order, n, v) for n, v, imp in decls]
            bspec = _body_selector_specificity(sel, classes)
            if bspec is not None:
                body += [(imp, bspec, order, n, v) for n, v, imp in decls]
            # html:has(> body.x) applies to <html> when the body has class x
            m = re.fullmatch(r"html:has\(>\s*body((?:\.[\w-]+)+)\)", sel)
            if m and all(c in classes for c in m.group(1).split(".")[1:]):
                html += [(imp, 20, order, n, v) for n, v, imp in decls]
    return _cascade(html), _cascade(body)


_VAR = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*(?:\([^()]*\))*[^()]*))?\)")


def _resolve(value: str, scope: dict, seen=()) -> str:
    def sub(m):
        name, fallback = m.group(1), m.group(2)
        if name in seen:
            raise ValueError(f"var() cycle through {name}")
        if name in scope:
            return scope[name]
        if fallback is not None:
            return _resolve(fallback.strip(), scope, seen)
        raise KeyError(name)
    for _ in range(20):
        new = _VAR.sub(sub, value)
        if new == value:
            return value
        value = new
    raise ValueError("var() nesting too deep")


def _element(decl: dict, inherited: dict) -> dict:
    """Computed custom properties of one element: its own declarations resolve
    var() against this element's values; everything else is inherited."""
    out = dict(inherited)
    memo = {}

    def value_of(name, stack):
        if name in memo:
            return memo[name]
        if name not in decl:
            return inherited[name]
        if name in stack:
            raise ValueError(f"var() cycle through {name}")
        refs = {m[0] for m in _VAR.findall(decl[name])}
        scope = {}
        for ref in refs:
            try:
                scope[ref] = value_of(ref, stack | {name})
            except KeyError:
                pass                      # unresolved: var() fallback applies
        memo[name] = _resolve(decl[name], scope)
        return memo[name]

    for name in decl:
        out[name] = value_of(name, frozenset())
    return out


def computed(css: str, theme: str = "pf2e"):
    """Resolved custom properties on <html> and as seen inside <body>."""
    html_decl, body_decl = declared(css, theme)
    html = _element(html_decl, {})
    return html, _element(body_decl, html)


def is_ds_name(name: str) -> bool:
    return name.startswith(DS_PREFIXES) or name in DS_NAMES


def render_ds_tokens(css: str) -> str:
    html, _ = computed(css, "pf2e")
    names = [n for n in html if is_ds_name(n)]
    lines = [
        "/* GENERATED by tools/design_tokens.py from static/css/system.css -- do not edit.",
        "   Design-system tokens (DS-01) with the canonical PF2e values, for pages that do",
        "   not load system.css (_account_base.html, splash.html). Custom properties only,",
        "   no element rules. tests/test_ds_tokens.py fails if this drifts. */",
        ":root {",
    ]
    lines += [f"    {n}: {html[n]};" for n in names]
    lines.append("}")
    _, media = parse(css)
    blocks = {}
    for prelude, sels, decls, _ in media:
        if ":root" not in sels:
            continue
        kept = [(n, v) for n, v, _ in decls if is_ds_name(n)]
        if kept:
            blocks.setdefault(prelude, []).extend(kept)
    for prelude, decls in blocks.items():
        lines.append("")
        lines.append(f"{prelude} {{")
        lines.append("    :root {")
        lines += [f"        {n}: {v};" for n, v in decls]
        lines.append("    }")
        lines.append("}")
    return "\n".join(lines) + "\n"


def main(argv):
    css = SYSTEM_CSS.read_text(encoding="utf-8")
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd == "generate":
        DS_TOKENS_CSS.write_text(render_ds_tokens(css), encoding="utf-8", newline="\n")
        print(f"wrote {DS_TOKENS_CSS.relative_to(ROOT)}")
        return 0
    if cmd == "check":
        want = render_ds_tokens(css)
        have = DS_TOKENS_CSS.read_text(encoding="utf-8") if DS_TOKENS_CSS.exists() else ""
        if have.replace("\r\n", "\n") != want:
            print("static/css/ds-tokens.css is out of date: run python tools/design_tokens.py generate")
            return 1
        print("ds-tokens.css matches system.css")
        return 0
    if cmd == "show":
        theme = argv[2] if len(argv) > 2 else "pf2e"
        _, body = computed(css, theme)
        for n in sorted(body):
            if is_ds_name(n):
                print(f"{n}: {body[n]}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
