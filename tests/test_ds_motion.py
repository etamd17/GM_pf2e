"""DS-02: the motion base stays inside its rules (DESIGN_DIRECTION.md 6.1-6.5)."""
import re
from pathlib import Path

import pytest

from tools.ui_screens import motion_problems

ROOT = Path(__file__).resolve().parents[1]
DS = ROOT / "static" / "css" / "ds.css"
SYSTEM = ROOT / "static" / "css" / "system.css"
MOTION_CLASSES = ("ds-press", "ds-enter", "ds-panel", "ds-collapse", "ds-details", "ds-chevron",
                  "ds-skeleton", "ds-shimmer", "ds-edge-fade")
ANIMATABLE = {"transform", "opacity", "color", "background-color", "border-color", "outline-color",
              "grid-template-rows"}
# Not motion a person sees as travel. Each is allowed only where it is needed.
EXCEPTIONS = {"visibility": (".ds-collapse",),
              "overflow": (".ds-collapse > *", "::details-content"),
              "height": ("::details-content",),            # named by 6.4
              "content-visibility": ("::details-content",)}
# The only rules that touch existing pages: static forms of two cues the block removes.
EXISTING_PAGE_RULES = {"body .gen-content.rerolling", "body .cp-chip-flash"}
UNIVERSAL = "*, *::before, *::after"


def _ds():
    return re.sub(r"/\*.*?\*/", "", DS.read_text(encoding="utf-8"), flags=re.S)


def _rules(css):
    """(selector, body) for every innermost rule, keyframe steps included."""
    return [(s.strip(), b) for s, b in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def _values(body, *names):
    """Values of the named declarations, prefixed or not, with or without a final semicolon."""
    pattern = r"(?<![\w-])(?:-webkit-)?(?:" + "|".join(names) + r")\s*:\s*([^;]+)"
    return [v.strip() for v in re.findall(pattern, body)]


def _block(css, opening):
    """The body of a brace-matched block, so a rule outside it cannot count as inside."""
    start = css.index(opening) + len(opening)
    start = css.index("{", start) + 1
    depth, i = 1, start
    while depth:
        depth += {"{": 1, "}": -1}.get(css[i], 0)
        i += 1
    return css[start:i - 1]


def _reduce():
    return _block(_ds(), "@media (prefers-reduced-motion: reduce)")


def test_t_tokens_change_curve_only():
    css = SYSTEM.read_text(encoding="utf-8")
    assert "--t-fast: 120ms var(--ease-standard);" in css
    assert "--t-base: 180ms var(--ease-standard);" in css
    assert "--t-slow: 280ms var(--ease-standard);" in css
    block = re.search(r"@media \(prefers-reduced-motion: reduce\) \{\s*:root \{(.*?)\}", css, re.S).group(1)
    assert all(f"--t-{n}: 1ms;" in block for n in ("fast", "base", "slow"))


def test_the_reduced_motion_block_is_the_one_in_the_plan():
    block = _reduce()
    rule = dict(_rules(block))[UNIVERSAL]
    assert "animation-duration: 0.01ms !important;" in rule
    assert "animation-iteration-count: 1 !important;" in rule
    assert "scroll-behavior: auto !important;" in rule
    # Transitions are not forced to zero: fades keep their token duration (6.3).
    assert "transition" not in rule
    # Exactly one copy, and only inside the query.
    assert _ds().count(UNIVERSAL) == 1
    # Multipliers stay declared once, in the token files.
    assert "--motion-distance:" not in _ds() and "--motion-scale-from:" not in _ds()


def test_important_only_in_the_global_reduced_motion_rule():
    for selector, body in _rules(_ds()):
        if "!important" in body:
            assert selector == UNIVERSAL


def test_no_rule_reaches_an_existing_page():
    """Every selector needs a new .ds-* class, bar the global rule and the two listed cues."""
    in_reduce = {s for s, _ in _rules(_reduce())}
    seen = set()
    for selector, _ in _rules(_ds()):
        if selector == UNIVERSAL or selector.startswith(("from", "to", "0%", "50%")):
            continue
        for part in selector.split(","):
            part = part.strip()
            if part in EXISTING_PAGE_RULES:
                assert part in in_reduce, part
                seen.add(part)
            else:
                assert ".ds-" in part, part
    assert seen == EXISTING_PAGE_RULES


def test_transitions_and_keyframes_use_allowed_properties():
    css = _ds()
    assert "blur(" not in css and "backdrop-filter" not in css
    assert "transition-property" not in css
    for selector, body in _rules(css):
        for value in _values(body, "transition"):
            if value == "none":
                continue
            for item in re.split(r",(?![^(]*\))", value):
                prop = item.split()[0]
                assert prop != "all"
                if prop not in ANIMATABLE:
                    assert prop in EXCEPTIONS and any(where in selector for where in EXCEPTIONS[prop]), (selector, prop)
    frames = re.findall(r"@keyframes ([\w-]+) \{(.*?\})\s*\}", css, re.S)
    assert {name for name, _ in frames} == {"ds-fade-rise", "ds-panel-swap", "ds-pulse", "ds-shimmer"}
    for name, body in frames:
        props = set(re.findall(r"([a-z-]+)\s*:", body))
        assert props <= ({"background-position"} if name == "ds-shimmer" else {"opacity", "transform"}), name


def test_no_animation_holds_its_end_state():
    """A held transform traps position:fixed children and keeps a stacking context."""
    for _, body in _rules(_ds()):
        for value in _values(body, "animation", "animation-fill-mode"):
            assert not re.search(r"\b(both|forwards)\b", value), value


def test_every_transform_stays_inside_the_budget():
    """Whitelist of shapes, so another function or a negative value cannot slip by."""
    travel = re.compile(r"translateY\(calc\((-?\d+)px \* var\(--motion-distance\)\)\)$")
    seen = 0
    for selector, body in _rules(_ds()):
        for value in _values(body, "transform"):
            seen += 1
            moved = travel.match(value)
            if moved:
                assert abs(int(moved.group(1))) <= 8, value
            elif value == "scale(var(--motion-scale-from))":
                assert selector == ".ds-press:active"
            elif value == "rotate(90deg)":
                assert "ds-chevron" in selector
            else:
                assert value == "none", value
    assert seen >= 5
    tokens = (ROOT / "static" / "css" / "ds-tokens.css").read_text(encoding="utf-8")
    assert float(re.search(r"--motion-scale-from: ([\d.]+);", tokens).group(1)) >= 0.96


def test_no_press_on_legacy_selectors():
    for selector, _ in _rules(_ds()):
        if ":active" in selector:
            assert selector == ".ds-press:active"


@pytest.mark.parametrize("name", MOTION_CLASSES)
def test_no_template_or_script_uses_a_motion_class_yet(name):
    pattern = re.compile(r"\b" + name + r"\b")
    roots = [ROOT / "templates", ROOT / "static" / "js"]
    hits = [p for r in roots for p in r.rglob("*") if p.is_file() and p.suffix in {".html", ".js"}
            and pattern.search(p.read_text(encoding="utf-8", errors="replace"))]
    assert not hits, hits


@pytest.mark.parametrize(("row", "bad"), [
    ({"name": "x", "target": "div", "duration": 0.01, "iterations": 1}, False),
    ({"name": "x", "target": "div", "duration": 0, "iterations": 1}, False),
    ({"name": "x", "target": "div", "duration": 0.01, "iterations": float("inf")}, True),
    ({"name": "x", "target": "div", "duration": 0.01, "iterations": None}, True),
    ({"name": "x", "target": "div", "duration": 2000, "iterations": 1}, True),
    ({"name": "x", "target": "div", "duration": 0.01, "iterations": 3}, True),
    ({"name": "x", "target": "div", "duration": "auto", "iterations": 1}, True),
])
def test_motion_audit_rule(row, bad):
    assert bool(motion_problems([row])) is bad


def test_motion_audit_reports_a_repeat_once():
    row = {"name": "x", "target": "div", "duration": 2000, "iterations": 1}
    assert len(motion_problems([row, row])) == 1
