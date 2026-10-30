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
    "pf2e": ("system-pf2e",),
    "stormlight": ("system-cosmere",),
    "mistborn": ("system-cosmere", "cosmere-mistborn"),
    # D&D 5e sets no surface, text or status tokens (dnd5e.css), so its shared
    # controls render the PF2e values until the DS-24a mockup (Q4). Its HP tiers
    # are its own (section 1b).
    "dnd5e": ("system-dnd5e",),
}

HTML_SELECTORS = {":root", "html", '[data-theme="dark"]'}

# Names copied into ds-tokens.css: every design-system token plus the existing
# spacing, radius and shadow scale they are built on.
DS_PREFIXES = ("--c-", "--fs-", "--lh-", "--fw-", "--pad-", "--gap-", "--layer-",
               "--dur-", "--ease-", "--motion-", "--sp-", "--shadow-1", "--shadow-2")
DS_NAMES = ("--r-sm", "--r-md", "--r-round")

# Selectors that could reach <html> or <body> but are not modelled by declared().
# A design-system token set through one would be silently ignored, so it is an error.
_REACHES_ROOT = re.compile(r"(?<![\w-])(?:body|html)(?![\w-])|:root|\.system-|\.cosmere-")
_HAS_BODY = re.compile(r"html:has\(>\s*body((?:\.[\w-]+)+)\)")


def _mask_strings(css: str) -> str:
    """The same text with the inside of every string replaced by underscores, so
    structure can be found by index without tripping on a quoted brace."""
    out, quote, i = [], None, 0
    while i < len(css):
        ch = css[i]
        if quote:
            if ch == "\\" and i + 1 < len(css):
                out.append("__")
                i += 2
                continue
            if ch == quote:
                quote = None
                out.append(ch)
            else:
                out.append("\n" if ch == "\n" else "_")
        else:
            if ch in "\"'":
                quote = ch
            out.append(ch)
        i += 1
    return "".join(out)


def _strip_comments(css: str) -> str:
    masked = _mask_strings_and_comments(css)
    return "".join(c for c, m in zip(css, masked) if m != "\0")


def _mask_strings_and_comments(css: str) -> str:
    """Mark comment characters with NUL; quotes inside a comment do not open a
    string, and comment markers inside a string do not open a comment."""
    out, quote, i = [], None, 0
    while i < len(css):
        ch = css[i]
        if quote:
            if ch == "\\" and i + 1 < len(css):
                out.append(css[i:i + 2])
                i += 2
                continue
            if ch == quote:
                quote = None
            out.append(ch)
            i += 1
        elif css.startswith("/*", i):
            end = css.find("*/", i + 2)
            end = len(css) if end < 0 else end + 2
            out.append("\0" * (end - i))
            i = end
        else:
            if ch in "\"'":
                quote = ch
            out.append(ch)
            i += 1
    return "".join(out)


def parse(css: str):
    """Return (rules, media_rules) as lists of (selectors, declarations, order).

    Only plain rule blocks are kept; at-rule contents are returned separately
    with their prelude so the media overrides can be mirrored."""
    css = _strip_comments(css)
    masked = _mask_strings(css)
    rules, media, order = [], [], 0
    i, n = 0, len(css)
    while i < n:
        brace = masked.find("{", i)
        if brace < 0:
            break
        # A statement at-rule (@import ...;) ends at its semicolon, not at a brace.
        i += masked[i:brace].rfind(";") + 1
        prelude = css[i:brace].strip()
        depth, j = 1, brace + 1
        while j < n and depth:
            depth += {"{": 1, "}": -1}.get(masked[j], 0)
            j += 1
        body = css[brace + 1:j - 1]
        if prelude.startswith("@"):
            inner_rules, inner_media = parse(body) if _is_rule_list(prelude) else ([], [])
            if prelude.startswith("@media"):
                for sels, decls, _ in inner_rules:
                    media.append((prelude, sels, decls, order))
                    order += 1
                _refuse_tokens(prelude, [m[2] for m in inner_media])
            else:
                _refuse_tokens(prelude, [r[1] for r in inner_rules]
                               + [m[2] for m in inner_media])
        else:
            rules.append(([s.strip() for s in prelude.split(",")], _declarations(body), order))
            order += 1
        i = j
    return rules, media


def _is_rule_list(prelude: str) -> bool:
    """At-rules whose body holds style rules (not @font-face, @keyframes, ...)."""
    return prelude.startswith(("@media", "@supports", "@layer", "@container", "@scope"))


def _refuse_tokens(prelude: str, declaration_lists) -> None:
    """Conditions other than one level of @media are not modelled, so a token set
    inside one would come out wrong without any sign of it."""
    for decls in declaration_lists:
        for name, _, _ in decls:
            if is_ds_name(name):
                raise ValueError(f"{name} is set inside {prelude!r}, which this tool does "
                                 "not model: extend tools/design_tokens.py")


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
        flag = re.search(r"!\s*important$", value, flags=re.I)
        if flag:
            value = value[: flag.start()].strip()
        out.append((name, value, bool(flag)))
    return out


def _split_top(text: str, sep: str):
    parts, depth, start = [], 0, 0
    for k, ch in enumerate(_mask_strings(text)):
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
            # html:has(> body.x) applies to <html> when the body has class x;
            # :has() counts as its argument, so html + body + one class per .x.
            m = _HAS_BODY.fullmatch(sel)
            if m:
                needed = m.group(1).split(".")[1:]
                if all(c in classes for c in needed):
                    html += [(imp, 2 + 10 * len(needed), order, n, v) for n, v, imp in decls]
            elif (sel not in HTML_SELECTORS and not _is_body_selector(sel)
                  and _REACHES_ROOT.search(sel)):
                for name, _, _ in decls:
                    if is_ds_name(name):
                        raise ValueError(f"{name} is set on {sel!r}, which this tool does not "
                                         "model: extend tools/design_tokens.py")
    return _cascade(html), _cascade(body)


def _is_body_selector(selector: str) -> bool:
    return re.fullmatch(r"body(?:\.[\w-]+)*", selector) is not None


_VAR_OPEN = re.compile(r"(?<![\w-])var\(")


def _var_calls(value: str):
    """Each top-level var() in a value: (start, end, name, fallback or None)."""
    calls, pos = [], 0
    while True:
        m = _VAR_OPEN.search(value, pos)
        if not m:
            return calls
        depth, k = 1, m.end()
        while k < len(value) and depth:
            depth += {"(": 1, ")": -1}.get(value[k], 0)
            k += 1
        if depth:
            raise ValueError(f"unbalanced var() in {value!r}")
        parts = _split_top(value[m.end():k - 1], ",")
        fallback = ",".join(parts[1:]).strip() if len(parts) > 1 else None
        calls.append((m.start(), k, parts[0].strip(), fallback))
        pos = k


def _refs(value: str) -> set:
    """Every custom property a value can read, fallbacks included."""
    out = set()
    for _, _, name, fallback in _var_calls(value):
        out.add(name)
        if fallback is not None:
            out |= _refs(fallback)
    return out


def _resolve(value: str, scope: dict) -> str:
    out, pos = [], 0
    for start, end, name, fallback in _var_calls(value):
        out.append(value[pos:start])
        if name in scope:
            out.append(scope[name])
        elif fallback is not None:
            out.append(_resolve(fallback, scope))
        else:
            raise KeyError(name)
        pos = end
    out.append(value[pos:])
    return "".join(out)


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
        scope = {}
        for ref in _refs(decl[name]):
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
    # Read at <body>, which sees everything <html> sets plus any body-only token.
    _, tokens = computed(css, "pf2e")
    names = [n for n in tokens if is_ds_name(n)]
    lines = [
        "/* GENERATED by tools/design_tokens.py from static/css/system.css -- do not edit.",
        "   Design-system tokens (DS-01) with the canonical PF2e values, for pages that do",
        "   not load system.css (_account_base.html, splash.html). Custom properties only,",
        "   no element rules. tests/test_ds_tokens.py fails if this drifts. */",
        ":root {",
    ]
    lines += [f"    {n}: {tokens[n]};" for n in names]
    lines.append("}")
    _, media = parse(css)
    blocks = {}
    for prelude, sels, decls, _ in media:
        if not {":root", "html", "body"} & set(sels):
            continue
        kept = [(n, f"{v} !important" if imp else v) for n, v, imp in decls if is_ds_name(n)]
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
