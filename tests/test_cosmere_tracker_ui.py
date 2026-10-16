"""UI-14: Cosmere tracker Revision B (phase and turn controls first).

Covers the rendered layout (setup disclosure, initiative toggle in the turn bar,
phase strip, phone scroll region), the unchanged control inventory, the phase
strip painter, and the bestiary "Strike:" actions now exposed as strikes.
"""
from __future__ import annotations

import uuid
from copy import deepcopy
from pathlib import Path

import pytest

import app as app_module
import systems.cosmere as cosmere

from tests.test_pf2e_tracker_ui import (
    _DOM,
    _Combatant,
    _canonical_inventory,
    _fingerprint,
    _render_tracker,
    _run_node,
)

ROOT = Path(app_module.__file__).parent
TEMPLATE = ROOT / "templates" / "tracker.html"

# Captured from origin/main at b5fa3a91 (the parent of UI-14) for a Cosmere
# render. IDs may only be added; inputs, selects, options and inline handlers
# must stay identical (the initiative toggle moves but keeps id and handler).
_COSMERE_BASELINE_IDS = frozenset("""
active-panel-wrap ap-body ap-name ap-subtitle boss-reveal br-epithet br-level br-name
btn-next-turn cca-atk-dmg cca-atk-mod cca-atk-name cca-cog cca-deflect cca-health
cca-level cca-name cca-phy cca-spi chat-fab chat-form chat-input chat-messages
chat-panel chat-unread chat-widget combat-log-body cos-adv-select cos-custom-adv
cos-init-mode cos-init-toggle cos-pc-select custom-roll-form custom-roll-input
dice-breakdown dice-panel dice-total dice-widget empty-state encounter-notes-collapsed
encounter-notes-display encounter-notes-input encounter-notes-text end-encounter-btn
gm-toast-container handout-content handout-image handout-panel handout-recipients
handout-title init-flourish initiative-list kb-help-card load-select log-toggle-arrow
main-layout mood-ctl mood-suggest multi-action-bar multi-cond-sel multi-count
multi-dmg-amount multi-dmg-type multi-summary multi-toggle-btn now-acting-name
pf2e-action-toast-container qs-cats qs-dialog qs-input qs-overlay qs-results
re-add-toggle re-f-repeat re-f-round re-f-show re-f-text re-f-title re-form
re-form-editing-id re-lane re-lane-count re-payload-conditions re-payload-damage
re-rows reb-round reb-text reb-title round-badge round-badge-num round-event-banner
save-load-panel save-name scene-mood screen-flash secret-dice-count secret-dice-sides
secret-label secret-modifier secret-pin-btn secret-roll-panel secret-roll-results
session-timer session-timer-btn session-timer-val sm-body sm-name sm-pos sm-subtitle
stat-modal sync-indicator trk-tools-btn trk-tools-menu trk-wordmark trk-wordmark-meta
turn-timer turn-timer-val
""".split())
_COSMERE_BASELINE = {
    "inputs": (28, "fcf39e540860e8dcbf67692f691d64c1cbbe0b0a1c75023e5829200864c01ef6"),
    "selects": (7, "cee1ecb47d48fb355a4aab436a41207b4ab9b765fe98f232316a986baeff352f"),
    "options": (16, "798dc731b99f3daea698cd5d7a7e053aae18b2c1ed91ba3229e07a1286e89788"),
    "handlers": (74, "c1e7d45f0a9607dfd5c848d803d6c477e0fc638dd475b8018dbc4ae155522e09"),
}


def _cosmere_dom(monkeypatch, populated):
    return _DOM(_render_tracker(monkeypatch, system="cosmere", populated=populated))


def test_cosmere_render_keeps_its_control_inventory(monkeypatch):
    for populated in (False, True):
        inventory = _canonical_inventory(_cosmere_dom(monkeypatch, populated))
        missing = _COSMERE_BASELINE_IDS - set(inventory["ids"])
        assert not missing, "UI-14 removed rendered IDs: " + ", ".join(sorted(missing))
        for group, expected in _COSMERE_BASELINE.items():
            assert _fingerprint(inventory[group]) == expected, (group, populated)


def test_cosmere_setup_disclosure_is_open_only_while_empty(monkeypatch):
    empty = _cosmere_dom(monkeypatch, False).by_class("cos-encounter-setup")
    populated = _cosmere_dom(monkeypatch, True).by_class("cos-encounter-setup")
    assert len(empty) == len(populated) == 1
    assert empty[0].tag == "details"
    assert "open" in empty[0].attrs
    assert "open" not in populated[0].attrs
    summary = next(c for c in empty[0].children if c.tag == "summary")
    assert summary.text_content().startswith("Encounter setup")
    assert any("tool-strip" in n.classes for n in empty[0].descendants())


def test_initiative_toggle_moves_into_the_turn_bar(monkeypatch):
    dom = _cosmere_dom(monkeypatch, True)
    toggle = dom.by_id("cos-init-toggle")
    assert toggle is not None and toggle.attrs.get("onclick") == "toggleCosmereInit()"
    hb_left = dom.by_class("hb-left")[0]
    assert toggle in list(hb_left.descendants())
    tool_strip = dom.by_class("tool-strip")[0]
    assert toggle not in list(tool_strip.descendants())


def test_phase_strip_and_scroll_region_are_cosmere_only(monkeypatch):
    cos = _cosmere_dom(monkeypatch, True)
    strip = cos.by_id("cos-phase-strip")
    assert strip is not None and strip.tag == "ol" and "hidden" in strip.attrs
    region = cos.by_class("cos-combat-scroll")
    assert len(region) == 1 and region[0].attrs.get("tabindex") == "0"
    assert "data-tracker-scroll" in region[0].attrs
    assert region[0].attrs.get("aria-describedby") == "cos-tracker-scroll-hint"
    assert cos.by_class("pf2e-encounter-setup") == []

    pf2e = _DOM(_render_tracker(monkeypatch, system="pf2e", populated=True))
    assert pf2e.by_id("cos-phase-strip") is None
    assert pf2e.by_class("cos-encounter-setup") == []
    assert pf2e.by_class("cos-combat-scroll") == []


def test_cosmere_styles_are_scoped_and_hidden_rules_present():
    text = TEMPLATE.read_text(encoding="utf-8")
    block = text.split("UI-14 · Cosmere phase-first layout", 1)[1].split("/* Utility */", 1)[0]
    for line in block.splitlines():
        line = line.strip()
        if line.endswith("{") and not line.startswith("@media"):
            assert line.startswith("body.system-cosmere"), line
    # Elements hidden by attribute need their own [hidden] rule (CLAUDE.md).
    assert "body.system-cosmere .trk .cos-phase-strip[hidden] { display:none !important; }" in block
    assert ".cos-combat-scroll-hint[hidden]" in block


def test_phase_strip_counts_phases_and_marks_the_acting_one():
    text = TEMPLATE.read_text(encoding="utf-8")
    painter = "const COSMERE_PHASES" + text.split("const COSMERE_PHASES", 1)[1].split(
        "// The multi-target damage modal", 1)[0]
    _run_node("""
const assert = require('node:assert/strict');
const items = [];
const strip = {hidden: true, replaceChildren: (...kids) => { items.length = 0; items.push(...kids); }};
const document = {
  getElementById: id => id === 'cos-phase-strip' ? strip : null,
  createElement: () => ({className: '', dataset: {}, attrs: {}, textContent: '',
                         setAttribute(k, v) { this.attrs[k] = v; }}),
};
let COSMERE_INIT_MODE = 'phases';
const c = (pc, choice, active=false) => ({system: 'cosmere', is_pc: pc, is_active: active,
  cosmere: {speed_choice: choice, phase: (choice === 'fast' ? 'fast' : 'slow') + '_' + (pc ? 'pc' : 'npc')}});
const STATE = {turn_index: 2, combatants: [c(true, 'fast'), c(true, 'slow'), c(false, 'slow'), c(false, 'slow')]};
""" + painter + """
renderCosmerePhaseStrip();
assert.equal(strip.hidden, false);
assert.deepEqual(items.map(i => i.dataset.phase), ['fast_pc', 'fast_npc', 'slow_pc', 'slow_npc']);
assert.deepEqual(items.map(i => i.textContent), ['Fast heroes \\u00b7 1', 'Fast NPCs \\u00b7 0', 'Slow heroes \\u00b7 1', 'Slow NPCs \\u00b7 2']);
assert.ok(items[3].className.includes('active'));
assert.equal(items[3].attrs['aria-current'], 'step');
assert.ok(items[1].className.includes('empty'));
// A same-shape SSE merge moves turn_index but leaves each row's is_active
// stale: the strip must follow turn_index.
STATE.combatants[2].is_active = true;
STATE.turn_index = 0;
renderCosmerePhaseStrip();
assert.ok(items[0].className.includes('active'), 'the acting hero is fast');
assert.equal(items[0].attrs['aria-current'], 'step');
assert.ok(!items[3].className.includes('active'), 'a stale is_active flag is ignored');
COSMERE_INIT_MODE = 'traditional';
renderCosmerePhaseStrip();
assert.equal(strip.hidden, true, 'traditional initiative hides the strip');
COSMERE_INIT_MODE = 'phases';
STATE.combatants = [];
renderCosmerePhaseStrip();
assert.equal(strip.hidden, true, 'an empty encounter hides the strip');
""")


def _bestiary(name):
    return next(d for d in cosmere.adversary_docs() if d.get("name") == name)


def test_bestiary_strike_actions_become_strikes():
    archer = cosmere.CosmereActor(_bestiary("Archer"))
    names = [s["name"] for s in archer.strikes]
    assert names == ["Knife", "Longbow"]
    longbow = next(s for s in archer.strikes if s["name"] == "Longbow")
    assert longbow["damage"] == "1d6" and longbow["type"] == "keen"
    assert longbow["skill"] == "hwp"
    assert longbow["bonus"] == archer.skills["hwp"]["mod"]
    # The action stays available to the action engine, flagged as a strike.
    flagged = {a["name"] for a in archer.actions if a.get("is_strike")}
    assert flagged == {"Strike: Knife", "Strike: Longbow"}
    assert {"Take Aim", "Immobilizing Shot"} <= {a["name"] for a in archer.actions}


def test_only_the_canonical_strike_name_counts():
    # A damaging skill-test ability that is not a basic "Strike:" stays an ability.
    for doc in cosmere.adversary_docs():
        actor = cosmere.CosmereActor(doc)
        for action in actor.actions:
            if action.get("is_strike"):
                assert action["name"].lower().startswith("strike:")
        # ...and every basic Strike with a skill test and a damage roll is one.
        flagged = {a["name"] for a in actor.actions if a.get("is_strike")}
        for item in doc.get("items", []):
            sys_ = item.get("system") or {}
            if (item.get("type") == "action" and item["name"].lower().startswith("strike:")
                    and (sys_.get("damage") or {}).get("formula")
                    and (sys_.get("activation") or {}).get("type") == "skill_test"):
                assert item["name"] in flagged, (doc["name"], item["name"])
    covered = sum(1 for d in cosmere.adversary_docs() if cosmere.CosmereActor(d).strikes)
    assert covered >= 120, covered  # 127 of 142 bestiary adversaries at UI-14


def test_full_stats_lists_the_strike_once(monkeypatch):
    actor = cosmere.CosmereActor(_bestiary("Archer"))
    actor.instance_id = "archer-1"
    with app_module.app.test_request_context("/"):
        payload = app_module._combatant_detail_payload(actor)
    assert [a["name"] for a in payload["attacks"]] == ["Knife", "Longbow"]
    assert not any(a["name"].startswith("Strike:") for a in payload["actions"])


def _strike_item(name, *, formula="1d6", activation="skill_test"):
    return {"_id": uuid.uuid4().hex, "type": "action", "name": name,
            "system": {"activation": {"type": activation, "skill": "hwp",
                                      "cost": {"type": "act", "value": 1}},
                       "damage": {"formula": formula, "type": "keen"} if formula else {}}}


def _adversary_with(*items):
    doc = deepcopy(_bestiary("Archer"))
    doc["_id"] = uuid.uuid4().hex
    doc["items"] = [it for it in doc["items"] if it.get("type") != "action"] + list(items)
    return doc


def test_strike_mapping_is_exact():
    actor = cosmere.CosmereActor(_adversary_with(
        _strike_item("Strike: Spear"),
        _strike_item("strike: Club"),                        # wrong case
        _strike_item("STRIKE: Axe"),                         # wrong case
        _strike_item("Strike: Shove", formula=""),           # no damage roll
        _strike_item("Strike: Bolt", activation="utility"),  # not a skill test
    ))
    assert [s["name"] for s in actor.strikes if s.get("source_action")] == ["Spear"]
    assert {a["name"] for a in actor.actions if a.get("is_strike")} == {"Strike: Spear"}
    assert {"strike: Club", "STRIKE: Axe", "Strike: Shove", "Strike: Bolt"} <= {
        a["name"] for a in actor.actions}
    # A non-string name is ignored, not an exception.
    odd = _strike_item("Strike: Spear")
    odd["name"] = 7
    cosmere.CosmereActor(_adversary_with(odd))


def test_kaiana_utility_strike_stays_an_ordinary_action():
    kaiana = next((d for d in cosmere.adversary_docs()
                   if any(i.get("name") == "Strike: Raysium Knife" for i in d.get("items", []))), None)
    if kaiana is None:
        pytest.skip("Kaiana is not in this bestiary")
    actor = cosmere.CosmereActor(kaiana)
    knife = [a for a in actor.actions if a["name"] == "Strike: Raysium Knife"]
    assert knife and not knife[0].get("is_strike")
    assert "Raysium Knife" not in [s["name"] for s in actor.strikes]


def test_legacy_custom_adversary_with_a_basic_strike_round_trips():
    from systems.cosmere.build import CosmereBuild
    from systems.cosmere.custom_adversaries import adversary_to_actor_document, normalize_store
    from systems.cosmere.rules.schema import to_dict

    legacy = _adversary_with(_strike_item("Strike: Knife", formula="1d4"))
    store = normalize_store([legacy], binding=to_dict(CosmereBuild().rules_binding))
    rich = store["adversaries"][0]
    names = [a["name"] for a in rich["actions"]]
    assert names.count("Strike: Knife") == 1 and "Knife" not in names
    actor = cosmere.CosmereActor(adversary_to_actor_document(rich))
    assert [s["name"] for s in actor.strikes].count("Knife") == 1


def _tracker_state_for(monkeypatch, combatant, mode="phases"):
    monkeypatch.setattr(app_module, "_cosmere_initiative_mode", lambda: mode)
    app_module.ACTIVE_ENCOUNTER[:] = [combatant]
    app_module.TURN_INDEX = 0
    app_module._invalidate_tracker_cache()
    try:
        return app_module._get_tracker_state()
    finally:
        app_module.ACTIVE_ENCOUNTER[:] = []
        app_module._invalidate_tracker_cache()


def _archer():
    actor = cosmere.CosmereActor(_bestiary("Archer"))
    actor.instance_id = "archer-1"
    return actor


def test_tracker_state_flags_cosmere_strikes_and_carries_the_mode(monkeypatch):
    state = _tracker_state_for(monkeypatch, _archer(), "traditional")
    assert state["cosmere_initiative"] == "traditional"
    acts = {a["name"]: a["is_strike"] for a in state["combatants"][0]["actions"]}
    assert acts["Strike: Longbow"] is True and acts["Take Aim"] is False


def test_pf2e_payloads_keep_their_shape(monkeypatch):
    pf2e = _Combatant()
    pf2e.actions = [{"name": "Claw", "actions": "1", "is_strike": True}]
    state = _tracker_state_for(monkeypatch, pf2e)
    assert "cosmere_initiative" not in state
    assert all("is_strike" not in a for c in state["combatants"] for a in c.get("actions", []))


def test_encounter_update_carries_the_mode(monkeypatch):
    sent = []
    monkeypatch.setattr(app_module, "sse_broadcast", lambda event, data, **kw: sent.append((event, data)))
    monkeypatch.setattr(app_module, "_cosmere_initiative_mode", lambda: "traditional")
    app_module.ACTIVE_ENCOUNTER[:] = [_archer()]
    app_module.TURN_INDEX = 0
    try:
        app_module._do_broadcast_encounter_state()
    finally:
        app_module.ACTIVE_ENCOUNTER[:] = []
        app_module._invalidate_tracker_cache()
    frames = [d for e, d in sent if e == "encounter_update"]
    assert frames and frames[-1]["cosmere_initiative"] == "traditional"


def _script_between(text, start, end):
    return start + text.split(start, 1)[1].split(end, 1)[0]


def test_a_remote_mode_change_reaches_every_tracker():
    text = TEMPLATE.read_text(encoding="utf-8")
    setter = _script_between(text, "function setCosmereInitMode", "function toggleCosmereInit")
    apply_state = _script_between(text, "function applyState(data)", "/* ──────────────── Render")
    sse = text.split("evtSource.addEventListener('encounter_update'", 1)[1].split("render();", 1)[0]
    assert "setCosmereInitMode(sse.cosmere_initiative)" in sse
    _run_node("""
const assert = require('node:assert/strict');
const label = {textContent: 'Fast / Slow phases'};
const document = {getElementById: id => id === 'cos-init-mode' ? label : null};
let COSMERE_INIT_MODE = 'phases';
let STATE = {combatants: []};
let modeAtRender = null;
const render = () => { modeAtRender = COSMERE_INIT_MODE; };
const renderEncounterNotes = () => {};
const _syncPf2eEncounterSetup = () => {};
""" + setter + "\n" + apply_state + """
applyState({combatants: [{}], cosmere_initiative: 'traditional'});
assert.equal(modeAtRender, 'traditional', 'the mode is updated before rendering');
assert.equal(label.textContent, 'Traditional (d20+Speed)');
applyState({combatants: [{}]});
assert.equal(COSMERE_INIT_MODE, 'traditional', 'a payload without the field leaves the mode alone');
applyState({combatants: [{}], cosmere_initiative: 'phases'});
assert.equal(modeAtRender, 'phases');
assert.equal(label.textContent, 'Fast / Slow phases');
""")


_INSPECTOR_HARNESS = r"""
const assert = require('node:assert/strict');
const els = {};
const el = id => els[id] || (els[id] = {id, innerHTML: '', textContent: '', style: {}, dataset: {},
  classList: {add() {}, remove() {}, toggle() {}, contains() { return false; }},
  querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, addEventListener() {}});
const document = {getElementById: el, body: {classList: {contains: c => c === 'system-cosmere'}},
  querySelector() { return null; }, querySelectorAll() { return []; }};
const stub = () => '';
const [_decorateActiveNoteLink, condChipClass, conditionTooltipFor, cosmereAttrBoxes, cosmereStatBoxes,
  costPips, hpBarClass, hpPctColor, initials, updateDamagePreview] = Array(10).fill(stub);
const escapeHtml = s => String(s ?? '');
let _inspectPinned = false, _inspectId = null;
const STATE = {turn_index: 0, combatants: [{instance_id: 'a', name: 'Archer', system: 'cosmere', is_pc: false,
  current_hp: 10, max_hp: 10, conditions: {}, cosmere: {tier: 1, phase: 'slow_npc', speed_choice: 'slow'},
  strikes: [{name: 'Longbow', hit: '+4', damage: '1d6'}],
  actions: [{name: 'Strike: Longbow', is_strike: true, description: ''},
            {name: 'Take Aim', is_strike: false, description: 'Aim.'}]}]};
"""


def test_inspector_lists_a_flagged_strike_once(monkeypatch):
    html = _render_tracker(monkeypatch, system="cosmere", populated=True)
    html = html if isinstance(html, str) else html.get_data(as_text=True)
    panel = _script_between(html, "function renderActivePanel()", "\n}\n") + "\n}\n"
    # The page's own constants (condition lists, damage types) and type picker.
    consts = _script_between(html, "const AJAX_HEADERS", "// Pick the damage-type vocabulary")
    consts += _script_between(html, "function damageTypeListFor", "\n}\n") + "\n}\n"
    _run_node(_INSPECTOR_HARNESS + consts + panel + r"""
renderActivePanel();
const html = Object.values(els).map(e => e.innerHTML).join('\n');
assert.ok(html.includes('Longbow'), 'the strike is listed');
assert.ok(!html.includes('Strike: Longbow'), 'the flagged action is not listed again');
assert.ok(html.includes('Take Aim'), 'ordinary abilities stay');
""")


def test_cosmere_setup_follows_empty_and_populated_transitions():
    text = TEMPLATE.read_text(encoding="utf-8")
    helper = text.split("const _pf2eEncounterSetup", 1)[1].split("function toggleSaveLoadPanel", 1)[0]
    _run_node("""
const assert = require('node:assert/strict');
let STATE = {combatants: []};
const setup = {open: true};
const document = {getElementById: id => id === 'cos-encounter-setup' ? setup : null};
const _pf2eEncounterSetup
""" + helper + """
STATE.combatants = [{}];
_syncPf2eEncounterSetup(STATE.combatants);
assert.equal(setup.open, false, 'first combatant closes setup');
setup.open = true;
_syncPf2eEncounterSetup(STATE.combatants);
assert.equal(setup.open, true, 'ordinary repaint preserves a manual open');
STATE.combatants = [];
_syncPf2eEncounterSetup(STATE.combatants);
assert.equal(setup.open, true, 'clearing the last combatant opens setup');
setup.open = false;
_syncPf2eEncounterSetup(STATE.combatants);
assert.equal(setup.open, false, 'ordinary empty repaint preserves a manual close');
""")
