"""UI-08 PF2e tracker layout and compatibility contract.

The tracker template is shared by PF2e and Cosmere.  These tests pin the
server-rendered PF2e controls before the Revision B layout moves them, then
check that the new setup disclosure and small-screen combat-table affordance
exist only in PF2e.  Runtime combat behavior remains covered by the existing
tracker suites.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess

import app as app_module
import pytest


_VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str | None]
    parent: "_Node | None" = None
    children: list["_Node"] = field(default_factory=list)
    data: list[str] = field(default_factory=list)

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def descendants(self):
        for child in self.children:
            yield child
            yield from child.descendants()

    def text_content(self) -> str:
        pieces = list(self.data)
        for child in self.children:
            pieces.append(child.text_content())
        return " ".join(" ".join(pieces).split())


class _DOM(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {})
        self._stack = [self.root]
        self.feed(html)

    @property
    def nodes(self) -> list[_Node]:
        return list(self.root.descendants())

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs), self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in _VOID_ELEMENTS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_ELEMENTS:
            self._stack.pop()

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data):
        self._stack[-1].data.append(data)

    def by_id(self, node_id: str) -> _Node | None:
        return next((node for node in self.nodes if node.attrs.get("id") == node_id), None)

    def by_class(self, class_name: str) -> list[_Node]:
        return [node for node in self.nodes if class_name in node.classes]


class _Combatant:
    """Small non-PC actor carrying exactly what tracker_view serializes."""

    def __init__(self):
        self.instance_id = "test-goblin"
        self.name = "Test Goblin"
        self.is_pc = False
        self.system = "pf2e"
        self.initiative = 17
        self.level = 1
        self.ac = 16
        self.current_hp = 20
        self.hp = 20
        self.fort = 6
        self.ref = 8
        self.will = 4
        self.perception = 7
        self.speed = 25
        self.conditions = {}
        self.strikes = []
        self.actions = []
        self.immunities = []
        self.resistances = []
        self.weaknesses = []
        self.traits = []


def _render_tracker(monkeypatch, *, system="pf2e", populated=False):
    encounter_dir = Path(app_module.__file__).parent / ".pytest_cache" / "ui08-render"
    encounter_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_module, "_active_system", lambda: system)
    monkeypatch.setattr(app_module, "_is_gm", lambda: True)
    monkeypatch.setattr(app_module, "_loaded_encounter_dir", lambda: str(encounter_dir))
    monkeypatch.setattr(app_module, "MONSTER_LIBRARY", {})
    monkeypatch.setattr(app_module, "PARTY_LIBRARY", {})
    monkeypatch.setattr(app_module, "TURN_REMINDERS", [])
    if system == "cosmere":
        monkeypatch.setattr(app_module.systems.cosmere, "adversary_docs", lambda: [])
        monkeypatch.setattr(app_module, "_load_cosmere_custom_adversaries", lambda: [])
        monkeypatch.setattr(app_module, "_list_cosmere_pcs", lambda: [])
    app_module.ACTIVE_ENCOUNTER[:] = [_Combatant()] if populated else []
    app_module.TURN_INDEX = 0
    app_module.ROUND_NUMBER = 1
    app_module._invalidate_tracker_cache()
    try:
        with app_module.app.test_request_context("/tracker"):
            return app_module.tracker_view()
    finally:
        app_module.ACTIVE_ENCOUNTER[:] = []
        app_module.TURN_INDEX = 0
        app_module.ROUND_NUMBER = 1
        app_module._invalidate_tracker_cache()


def _canonical_inventory(dom: _DOM) -> dict[str, list]:
    nodes = dom.nodes
    inputs = []
    selects = []
    options = []
    handlers = []
    for node in nodes:
        attrs = node.attrs
        if node.tag in {"input", "textarea"}:
            inputs.append(tuple((key, attrs.get(key)) for key in (
                "tag", "id", "name", "type", "value", "min", "max", "maxlength",
                "placeholder", "multiple",
            ) if key == "tag" or key in attrs))
            inputs[-1] = (("tag", node.tag),) + tuple(
                pair for pair in inputs[-1] if pair[0] != "tag"
            )
        if node.tag == "select":
            selects.append(tuple((key, attrs.get(key)) for key in (
                "id", "name", "multiple", "aria-label", "title",
            ) if key in attrs))
        if node.tag == "option":
            parent_id = node.parent.attrs.get("id") if node.parent else None
            options.append((parent_id, attrs.get("value"), node.text_content(),
                            "selected" in attrs, "disabled" in attrs))
        for attr, value in attrs.items():
            if attr.startswith("on"):
                handlers.append((node.tag, attrs.get("id"), attr, value))
    return {
        "ids": sorted({node.attrs["id"] for node in nodes if node.attrs.get("id")}),
        "inputs": sorted(inputs, key=repr),
        "selects": sorted(selects, key=repr),
        "options": sorted(options, key=repr),
        "handlers": sorted(handlers, key=repr),
    }


def _fingerprint(rows: list) -> tuple[int, str]:
    payload = json.dumps(rows, ensure_ascii=True, separators=(",", ":"))
    return len(rows), sha256(payload.encode()).hexdigest()


def _run_node(script: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the tracker keyboard behavior test")
    result = subprocess.run(
        [node, "-e", script], capture_output=True, text=True,
        cwd=Path(app_module.__file__).parent,
    )
    assert result.returncode == 0, result.stderr or result.stdout


# Captured from origin/main at 9c9e4c15 (the parent commit for UI-08).  New
# layout-only IDs may be added, so existing IDs are checked as a subset.  Form
# fields, options and inline handlers must remain byte-for-byte equivalent.
_BASELINE_IDS: frozenset[str] = frozenset("""
active-panel-wrap ap-body ap-name ap-subtitle boss-reveal br-epithet br-level
br-name btn-next-turn cg-metal-aluminum cg-metal-bendalloy cg-metal-brass
cg-metal-bronze cg-metal-cadmium cg-metal-chromium cg-metal-copper
cg-metal-duralumin cg-metal-electrum cg-metal-gold cg-metal-iron
cg-metal-nicrosil cg-metal-pewter cg-metal-steel cg-metal-tin cg-metal-zinc
cg-order-bondsmiths cg-order-dustbringers cg-order-edgedancers cg-order-elsecallers
cg-order-lightweavers cg-order-skybreakers cg-order-stonewards
cg-order-truthwatchers cg-order-willshapers cg-order-windrunners cg-surge-abr
cg-surge-adh cg-surge-chs cg-surge-dvs cg-surge-grv cg-surge-ill cg-surge-prg
cg-surge-trp cg-surge-trs cg-surge-tsn chat-fab chat-form chat-input
chat-messages chat-panel chat-unread chat-widget check-request-panel cm-ac
cm-atk-dmg cm-atk-mod cm-atk-name cm-fort cm-hp cm-level cm-name cm-perc cm-ref
cm-speed cm-will combat-log-body cr-dc cr-skill cr-targets custom-monster-form
custom-roll-form custom-roll-input dice-breakdown dice-panel dice-total dice-widget
empty-state encounter-notes-collapsed encounter-notes-display
encounter-notes-input encounter-notes-text end-encounter-btn gm-toast-container
handout-content handout-image handout-panel handout-recipients handout-title
init-flourish initiative-list kb-help-card load-select log-toggle-arrow loot-cp
loot-gp loot-items loot-panel loot-pp loot-sp loot-target main-layout monster-dd
monster-dd-hint monster-search monster-search-wrap mood-ctl mood-suggest
multi-action-bar multi-cond-rounds multi-cond-sel multi-cond-val multi-count
multi-dmg-amount multi-dmg-type multi-save-dc multi-save-results multi-save-type
multi-summary multi-toggle-btn now-acting-name pc-select pf2e-action-toast-container
qs-cats qs-dialog qs-input qs-overlay qs-results re-add-toggle re-f-repeat
re-f-round re-f-show re-f-text re-f-title re-form re-form-editing-id
re-lane re-lane-count re-payload-conditions re-payload-damage re-rows reb-round
reb-text reb-title rn-adventurer rn-champion rn-cleric rn-druid rn-flame
rn-kineticist round-badge round-badge-num round-event-banner save-load-panel
save-name scene-mood screen-flash secret-dice-count secret-dice-sides secret-label
secret-modifier secret-pin-btn secret-roll-panel secret-roll-results session-timer
session-timer-btn session-timer-val sm-body sm-name sm-pos sm-subtitle stat-modal
sync-indicator trk-tools-btn trk-tools-menu trk-wordmark trk-wordmark-meta
turn-timer turn-timer-val xp-bar-fill xp-bar-wrap xp-diff-pill xp-party-level
xp-value
""".split())
_BASELINE_FINGERPRINTS: dict[str, tuple[int, str]] = {
    "inputs": (40, "7f3c32f459a64c7e43ba34bc63efc6c96d367a8434c1e32732c4a4b41c53c9b9"),
    "selects": (10, "b48af87310855dbcf6809b8f2ae9a112fe9262698b8266f8bdd852b90c63d525"),
    "options": (51, "d5a91a6451c2da9f014494c5fe2eff2de19c78a7242698f0ed2df9830eab1046"),
    "handlers": (85, "1aaafe6c784680a1781e2a41976e3f89a249594409bd966b4ad3870fa5c6f333"),
}


def test_pf2e_render_keeps_the_pre_ui08_control_inventory(monkeypatch):
    dom = _DOM(_render_tracker(monkeypatch))
    inventory = _canonical_inventory(dom)
    assert not (_BASELINE_IDS - set(inventory["ids"])), (
        "UI-08 removed existing rendered IDs: "
        + ", ".join(sorted(_BASELINE_IDS - set(inventory["ids"])))
    )
    for group, expected in _BASELINE_FINGERPRINTS.items():
        assert _fingerprint(inventory[group]) == expected, group


def test_pf2e_setup_is_open_only_while_the_encounter_is_empty(monkeypatch):
    empty = _DOM(_render_tracker(monkeypatch, populated=False))
    populated = _DOM(_render_tracker(monkeypatch, populated=True))
    empty_setup = empty.by_class("pf2e-encounter-setup")
    populated_setup = populated.by_class("pf2e-encounter-setup")
    assert len(empty_setup) == len(populated_setup) == 1
    assert empty_setup[0].tag == populated_setup[0].tag == "details"
    assert "open" in empty_setup[0].attrs
    assert "open" not in populated_setup[0].attrs

    summary = next((child for child in empty_setup[0].children if child.tag == "summary"), None)
    assert summary is not None
    assert summary.text_content() == "Encounter setup · add creatures, party, save/load, and mood"
    tool_strips = [node for node in empty_setup[0].descendants() if "tool-strip" in node.classes]
    assert len(tool_strips) == 1


def test_tracker_shortcuts_keep_native_keys_without_disabling_combat_shortcuts():
    template = Path(app_module.__file__).parent / "templates" / "tracker.html"
    text = template.read_text(encoding="utf-8")
    helper = "function _trackerKeepsNativeKey" + text.split(
        "function _trackerKeepsNativeKey", 1
    )[1].split("document.addEventListener('keydown'", 1)[0]
    _run_node("""
const assert = require('node:assert/strict');
const region = {
  tagName: 'DIV',
  scrollWidth: 800,
  clientWidth: 375,
  contains: node => node === button,
  closest: selector => selector === '[data-tracker-scroll]' ? region : null,
};
const button = {
  tagName: 'BUTTON',
  closest: selector => selector === '[data-tracker-scroll]' ? region
    : selector === 'button, a, [role="button"]' ? button : null,
};
// closest() matches a selector list the way the DOM does: any member matches.
const inSetup = id => selector => selector.split(',').map(s => s.trim()).includes(id) ? {} : null;
const summary = {tagName: 'SUMMARY', closest: inSetup('#pf2e-encounter-setup')};
const cosmereSummary = {tagName: 'SUMMARY', closest: inSetup('#cos-encounter-setup')};
""" + helper + """
assert.equal(_trackerKeepsNativeKey({target: summary, key: ' ', code: 'Space'}), true);
assert.equal(_trackerKeepsNativeKey({target: summary, key: 'n', code: 'KeyN'}), false);
assert.equal(_trackerKeepsNativeKey({target: cosmereSummary, key: ' ', code: 'Space'}), true);
assert.equal(_trackerKeepsNativeKey({target: cosmereSummary, key: 'n', code: 'KeyN'}), false);
assert.equal(_trackerKeepsNativeKey({target: button, key: ' ', code: 'Space'}), true);
assert.equal(_trackerKeepsNativeKey({target: button, key: 'n', code: 'KeyN'}), false);
assert.equal(_trackerKeepsNativeKey({target: button, key: 'Escape', code: 'Escape'}), false);
assert.equal(_trackerKeepsNativeKey({target: button, key: 'ArrowLeft', code: 'ArrowLeft'}), false);
assert.equal(_trackerKeepsNativeKey({target: region, key: 'ArrowRight', code: 'ArrowRight'}), true);
region.clientWidth = 800;
assert.equal(_trackerKeepsNativeKey({target: region, key: 'ArrowRight', code: 'ArrowRight'}), false);
assert.equal(_trackerKeepsNativeKey({target: region, key: 'n', code: 'KeyN'}), false);
""")


def test_setup_disclosure_tracks_only_empty_populated_transitions():
    template = Path(app_module.__file__).parent / "templates" / "tracker.html"
    text = template.read_text(encoding="utf-8")
    helper = text.split(
        "const _pf2eEncounterSetup", 1
    )[1].split("function toggleSaveLoadPanel", 1)[0]
    apply_state = text.split("function applyState(data)", 1)[1].split(
        "/* ──────────────── Render", 1
    )[0]
    assert "_syncPf2eEncounterSetup(STATE.combatants)" in apply_state
    _run_node("""
const assert = require('node:assert/strict');
let STATE = {combatants: []};
const setup = {open: true};
const document = {getElementById: id => id === 'pf2e-encounter-setup' ? setup : null};
const _pf2eEncounterSetup
""" + helper + """
STATE.combatants = [{}];
_syncPf2eEncounterSetup(STATE.combatants);
assert.equal(setup.open, false, 'first combatant closes setup');
setup.open = true;
_syncPf2eEncounterSetup(STATE.combatants);
assert.equal(setup.open, true, 'ordinary combat repaint preserves a manual open');
STATE.combatants = [];
_syncPf2eEncounterSetup(STATE.combatants);
assert.equal(setup.open, true, 'clearing the last combatant opens setup');
setup.open = false;
_syncPf2eEncounterSetup(STATE.combatants);
assert.equal(setup.open, false, 'ordinary empty repaint preserves a manual close');
""")


def test_pf2e_combat_table_has_a_focusable_mobile_scroll_region(monkeypatch):
    dom = _DOM(_render_tracker(monkeypatch))
    regions = dom.by_class("pf2e-combat-scroll")
    hints = dom.by_class("pf2e-combat-scroll-hint")
    assert len(regions) == len(hints) == 1
    region, hint = regions[0], hints[0]
    assert region.attrs.get("tabindex") == "0"
    assert region.attrs.get("role") == "region"
    assert region.attrs.get("aria-label")
    assert hint.attrs.get("id")
    assert region.attrs.get("aria-describedby") == hint.attrs["id"]
    assert re.search(r"swipe|scroll", hint.text_content(), re.I)
    assert any("grid-wrap" in node.classes for node in region.descendants())


def test_cosmere_render_has_none_of_the_pf2e_layout_wrappers(monkeypatch):
    dom = _DOM(_render_tracker(monkeypatch, system="cosmere"))
    assert not dom.by_class("pf2e-encounter-setup")
    assert not dom.by_class("pf2e-combat-scroll")
    assert not dom.by_class("pf2e-combat-scroll-hint")
    assert len(dom.by_class("tool-strip")) == 1
    assert len(dom.by_class("grid-wrap")) == 1


def test_revision_b_tracker_css_is_ported_without_preview_prefixes():
    template = (Path(app_module.__file__).parent / "templates" / "tracker.html").read_text(
        encoding="utf-8"
    )
    compact = re.sub(r"\s+", " ", template)
    required_selectors = (
        ".trk .hero-bar", ".trk .turn-now .v", ".trk .hb-btn",
        ".trk .combatant", ".trk .combatant.active", ".trk .nm",
        ".trk .nm-sub", ".trk .grid-head", ".trk .c-hp-quick",
        ".trk .c-hp-quick .qhp", ".trk .hero-stats", ".trk .hs-box",
        ".trk .hero-top", ".trk .section-h", ".pf2e-encounter-setup",
        ".pf2e-encounter-setup > summary", ".trk .hp-ribbon",
        ".pf2e-combat-scroll", ".pf2e-combat-scroll-hint",
    )
    for selector in required_selectors:
        assert selector in compact, selector
    assert "preview-session" not in compact
    assert "preview-refine" not in compact

    assert re.search(r"\.trk \.hb-btn\s*\{[^}]*min-height:\s*32px", compact)
    assert re.search(r"\.trk \.combatant\s*\{[^}]*min-height:\s*78px", compact)
    assert re.search(r"\.trk \.hero-bar\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\) auto", compact)
    desktop_scroll = re.search(r"\.pf2e-combat-scroll\s*\{([^}]*)\}", compact)
    assert desktop_scroll and "overflow-x" not in desktop_scroll.group(1)
    assert re.search(r"@media\s*\(max-width:\s*767px\).*?\.pf2e-combat-scroll\s*\{[^}]*overflow-x:\s*auto", compact)
    assert re.search(r"@media\s*\(max-width:[^)]+\)[^{]*\{.*?\.pf2e-combat-scroll-hint\s*\{[^}]*display:\s*block", compact)
