"""DS-03 source contracts: narrow cascade overrides, hover gates and frozen hooks."""
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
DS = ROOT / "static/css/ds.css"

# Exact selectors are deliberate: later page packages cannot silently widen this gate.
FOCUS_SELECTORS = {
    ":where(a, button, input, select, textarea, summary, [tabindex]):focus-visible",
    ":where(.ds-inset-focus):focus-visible",
    "body .nav-links :where(a, button):focus-visible",
    "body .builder-nav-list .nav-item:focus-visible",
    "body .rapid-bar .rapid-clear:focus-visible", "body .rapid-bar .rapid-btn:focus-visible",
    "body #pf2e-encounter-setup > summary:focus-visible",
    "body #cos-encounter-setup > summary:focus-visible",
    "body .cal-nav-btn:focus-visible",
    "body .bld .wiz-rail .wiz-tab:focus-visible",
    "body .mbb .stepper button:focus-visible",
    "body #equip-list .equip-item:focus-visible",
    "body #equip-list .equip-item.opacity-40:focus-visible",
    "body #equip-cat-btns .equip-cat:focus-visible",
    "body #mod-tabs .mod-tab-btn:focus-visible",
    'body [data-cond-row] > button[data-cond-act="toggle"]:focus-visible',
    "body .caster-view-toggle button:focus-visible",
    "body #tab-inventory #inv-list .invested-toggle:focus-visible",
    'body #tab-inventory #inv-list button[onclick^="removeInvItem("]:focus-visible',
    "body .tile__link:focus-visible",
    "body .tile__link:focus-visible::after",
    "body .card[data-char-name] > a.absolute.inset-0:focus-visible",
    "body .pin-fav-btn:focus-visible",
    "body .whisper-fab:focus-visible",
    "body .tab-bar-scroll .tab-btn:focus-visible",
    "body .tab-bar-scroll .tab-btn.active:focus-visible",
    "body #mod-list button:focus-visible",
    "body .cw-btn:focus-visible", "body .cw-btn.on:focus-visible",
    "body .cb-glossary > summary:focus-visible", "body .gms-xref:focus-visible",
    "body .tab-btn:focus-visible", "body .panel-card input:focus-visible",
    "body .panel-card select:focus-visible", "body .panel-card textarea:focus-visible",
    "body .hp-input:focus-visible", "body .tinput:focus-visible", "body .tselect:focus-visible",
    "body .d5-input:focus-visible", "body .d5-select:focus-visible", "body .d5-textarea:focus-visible",
    "body input:focus-visible", "body select:focus-visible", "body .field-input:focus-visible",
    "body .cb-search:focus-visible", "body .gen-toolbar-input:focus-visible",
    "body .gm-search-input:focus-visible", "body .form-group input:focus-visible",
    "body .form-group select:focus-visible", "body .chron-doc-row__rename:focus-visible",
    "body #nt-area:focus-visible", "body .feat-note-input:focus-visible",
    "body .inv-filter:focus-visible", "body .class-cheatsheet summary:focus-visible",
    "body .whisper-panel textarea:focus-visible", "body .trk .ts-search input:focus-visible",
    "body .trk .ts-select:focus-visible", "body .trk .c-init input:focus-visible",
    "body .trk .epithet-in:focus-visible", "body .trk .c-hp-quick .qhp-in:focus-visible",
    "body .trk .c-hp-ctrl input:focus-visible", "body .trk .c-hp-ctrl select:focus-visible",
    "body .trk .cp-add select:focus-visible", 'body .trk .cp-add input[type="number"]:focus-visible',
    "body .trk .pd-row input:focus-visible", "body .trk .tactics-area:focus-visible",
    'html[data-theme="hi-contrast"] body button:focus-visible',
    'html[data-theme="hi-contrast"] body .tb:focus-visible',
    r"body .focus\:outline-none:focus-visible",
    'body #tab-combat button[onclick^="deleteWeapon("]:focus-visible',
    'body #tab-feats button[onclick^="rollFeature("]:focus-visible',
    'body #tab-inventory button[onclick^="removeInvItem("]:focus-visible',
    'body #tab-inventory button[onclick^="deleteSessionNote("]:focus-visible',
    'body #tab-pets button[onclick^="removePet("]:focus-visible',
    'body #ui-retrain-list button[onclick^="initRetrainFeat("]:focus-visible',
    "body .gm-ref-card .gm-pin-btn:focus-visible",
    'body .card button[onclick*="setMyChar("]:focus-visible',
    'body .card button[onclick*="deleteChar("]:focus-visible',
}

IMPORTANT_FOCUS_SELECTORS = {
    'body [data-cond-row] > button[data-cond-act="toggle"]:focus-visible',
    "body .tab-btn:focus-visible", "body .panel-card input:focus-visible",
    "body .panel-card select:focus-visible", "body .panel-card textarea:focus-visible",
    "body .hp-input:focus-visible", "body .tinput:focus-visible", "body .tselect:focus-visible",
    'body .card button[onclick*="setMyChar("]:focus-visible',
    'body .card button[onclick*="deleteChar("]:focus-visible',
}

CHRONICLE_KEYBOARD_GATE = "body .chron-subnav:has(> .chron-tab:focus-visible)"
FOCUS_GEOMETRY_RULES = {
    CHRONICLE_KEYBOARD_GATE: {"padding-inline": "6px", "margin-inline": "-6px", "scroll-snap-type": "x mandatory"},
    CHRONICLE_KEYBOARD_GATE + " > .chron-tab": {"padding-inline": "6px", "margin-inline": "-6px"},
    CHRONICLE_KEYBOARD_GATE + " > .chron-tab:focus-visible": {"outline-offset": "-4px", "scroll-snap-align": "end"},
    CHRONICLE_KEYBOARD_GATE + ' > .chron-tab[aria-current="page"]::after': {"left": "6px", "right": "6px"},
}
FOCUS_SELECTORS |= FOCUS_GEOMETRY_RULES.keys()


def selectors(text):
    """Split selector lists, preserving commas inside :where/:is and attributes."""
    parts, start, depth, quote, escaped = [], 0, 0, None, False
    for i, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "," and not depth:
            parts.append(text[start:i].strip())
            start = i + 1
    return parts + [text[start:].strip()]


def test_global_ring_is_additive_and_the_inset_variant_only_moves_it():
    css = re.sub(r"/\*.*?\*/", "", DS.read_text(encoding="utf-8"), flags=re.S)
    rule = re.search(r":where\(a, button, input, select, textarea, summary, \[tabindex\]\):focus-visible\s*\{([^}]+)\}", css)
    assert rule, "The shared base ring is missing"
    assert "outline: 2px solid var(--c-focus);" in rule.group(1)
    assert "outline-offset: 2px;" in rule.group(1)
    assert "box-shadow" not in rule.group(1)
    assert re.search(r":where\(\.ds-inset-focus\):focus-visible\s*\{\s*outline-offset: -2px;\s*\}", css)


def test_every_important_focus_rule_explains_the_specific_suppression():
    raw = DS.read_text(encoding="utf-8")
    seen = set()
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", raw):
        header, body = match.groups()
        if "!important" not in body or ":focus-visible" not in header:
            continue
        comments = re.findall(r"/\*(.*?)\*/", header, re.S)
        assert comments and re.search(r"beats .*?(?:system\.css|player_view\.html):\d+", comments[-1], re.S), header
        clean = re.sub(r"/\*.*?\*/", "", header, flags=re.S).strip()
        assert set(selectors(clean)) <= IMPORTANT_FOCUS_SELECTORS
        seen.update(selectors(clean))
    assert seen == IMPORTANT_FOCUS_SELECTORS


def test_persistent_damage_focus_does_not_override_the_inline_error():
    css = re.sub(r"/\*.*?\*/", "", DS.read_text(encoding="utf-8"), flags=re.S)
    rule = re.search(r"body \.trk \.pd-row input:focus-visible\s*\{([^}]+)\}", css)
    assert rule and "outline:" in rule.group(1)
    assert "!important" not in rule.group(1)


def test_focus_rules_change_only_indicators_or_the_exact_chronicle_geometry_exception():
    css = re.sub(r"/\*.*?\*/", "", DS.read_text(encoding="utf-8"), flags=re.S)
    seen = set()
    for header, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if ":focus-visible" not in header:
            continue
        selected = set(selectors(header.strip()))
        assert selected <= FOCUS_SELECTORS, selected - FOCUS_SELECTORS
        seen.update(selected)
        declarations = []
        for declaration in body.split(";"):
            if not declaration.strip():
                continue
            name, separator, value = declaration.partition(":")
            name, value = name.strip(), value.strip()
            assert separator and re.fullmatch(r"[\w-]+", name) and value, declaration
            declarations.append((name, value))
        geometry = selected & FOCUS_GEOMETRY_RULES.keys()
        if geometry:
            assert len(selected) == 1
            expected = FOCUS_GEOMETRY_RULES[geometry.pop()]
            assert len(declarations) == len(expected) and dict(declarations) == expected, (
                "Unexpected focus geometry declarations", declarations)
        else:
            assert {name for name, _ in declarations} <= {"outline", "outline-offset", "opacity"}
    assert seen == FOCUS_SELECTORS


@pytest.mark.parametrize("extra", ["animation: x 1s", "animation: x 1s;"])
def test_focus_geometry_gate_rejects_an_extra_declaration_with_or_without_final_semicolon(
        monkeypatch, tmp_path, extra):
    source = DS.read_text(encoding="utf-8")
    pattern = r"(" + re.escape(CHRONICLE_KEYBOARD_GATE) + r"\s*\{)([^}]*)(\})"
    changed, replacements = re.subn(
        pattern, lambda match: match[1] + match[2].rstrip() + "\n    " + extra + "\n" + match[3],
        source, count=1)
    assert replacements == 1
    fixture = tmp_path / "focus-extra-declaration.css"
    fixture.write_text(changed, encoding="utf-8")
    monkeypatch.setitem(globals(), "DS", fixture)
    with pytest.raises(AssertionError, match="Unexpected focus geometry declarations"):
        test_focus_rules_change_only_indicators_or_the_exact_chronicle_geometry_exception()


def test_chronicle_geometry_exception_is_phone_only_and_keyboard_gated():
    css = re.sub(r"/\*.*?\*/", "", DS.read_text(encoding="utf-8"), flags=re.S)
    start = css.index("@media (max-width: 480px)")
    opening = css.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (css[end] == "{") - (css[end] == "}")
        end += 1
    block = css[opening + 1:end - 1]
    assert ":focus-within" not in block
    for selector in FOCUS_GEOMETRY_RULES:
        assert css.count(selector + " {") == 1
        assert selector + " {" in block


def test_three_inline_outline_suppressions_are_removed_without_new_hooks():
    source = (ROOT / "templates/base.html").read_text(encoding="utf-8")
    for control in ("qs-input", "chat-input", "journal-input"):
        tag = re.search(r'<(?:input|textarea)\b[^>]*\bid="' + control + r'"[^>]*>', source).group()
        assert not re.search(r"outline\s*:\s*none", tag)


def test_tile_hovers_are_gated_where_they_live():
    expected = {
        "static/css/system.css": (".choice-card:hover", ".tile:hover", ".tile:hover::before"),
        "templates/cosmere_gm.html": (".cos-hub-stage .tile:hover", ".cos-hub-stage .tile:hover::before"),
        "templates/player_view.html": (".card:hover",),
        "templates/cosmere_pcs.html": (".pc-card:hover",),
        "templates/cosmere_bestiary.html": (".cb-card:hover",),
        "templates/account_home.html": (".cc:hover", ".ch:hover"),
    }
    for name, rules in expected.items():
        source = (ROOT / name).read_text(encoding="utf-8")
        for selector in rules:
            start = source.index(selector + " ") if selector + " " in source else source.index(selector + "{")
            gate = source.rfind("@media (hover: hover) and (pointer: fine)", 0, start)
            assert gate != -1, (name, selector)
            # Braces from the gate to the selector remain inside that media block.
            prefix = source[gate:start]
            assert prefix.count("{") > prefix.count("}"), (name, selector)
