"""PF2e sheet snappiness: combat-hot persistent-damage actions update the strip
in place from the server's returned list instead of doing a full-page reload
(which blanked + rebuilt the ~10k-line sheet on every add/remove/flat-check).
"""
from __future__ import annotations

import json
import pathlib

import pytest

import app as app_module
from app import Character

_FIX = pathlib.Path(__file__).parent / 'fixtures' / 'kyle_l10.json'
_TEMPLATES = pathlib.Path(__file__).parent.parent / 'templates'


@pytest.fixture
def kyle(tmp_path, monkeypatch):
    raw = json.loads(_FIX.read_text())
    pc_file = tmp_path / 'Kyle.json'
    pc_file.write_text(json.dumps(raw), encoding='utf-8')
    monkeypatch.setitem(app_module.PARTY_LIBRARY, 'Kyle', Character(raw, file_path=str(pc_file)))
    monkeypatch.setattr(app_module, 'get_pc_file_path', lambda n: str(pc_file) if n == 'Kyle' else None)
    return 'Kyle'


def test_persistent_damage_renders_in_place(kyle):
    body = app_module.app.test_client().get('/player/sheet/Kyle').data.decode()
    assert 'function renderPersistentDamage' in body                       # the reusable in-place renderer
    # pdAddPrompt / pdRemove / pdFlatCheck (and the SSE handler) re-render from
    # the server's list rather than reloading the page.
    assert body.count('renderPersistentDamage(data.persistent_damage)') >= 3


def test_persistent_damage_handlers_dropped_the_reload(kyle):
    body = app_module.app.test_client().get('/player/sheet/Kyle').data.decode()
    # isolate just the three persistent-damage handlers (pdAddPrompt .. end of
    # pdFlatCheck) and confirm none of them reload the page anymore.
    start = body.index('async function pdAddPrompt')
    end = body.index("'pdFlatCheck:'")                     # inside the last pd handler's catch
    pd_block = body[start:end]
    assert pd_block.count('renderPersistentDamage(data.persistent_damage)') == 3
    assert 'location.reload' not in pd_block


# ── Derived-stat payload + in-place toggle painters (PR #29) ──────────────
# A condition / feature / two-hand change ripples into strike mods, save totals,
# skill totals and AC. The sheet repaints those in place from the pc_update
# `derived` block instead of a full-page reload. These guard the data contract
# between the server payload and the client painters so a future shape change
# to pc.fort/pc.skills/pc.attacks can't silently break them.

def test_pc_update_derived_payload_matches_pc(kyle, monkeypatch):
    pc = app_module.PARTY_LIBRARY['Kyle']
    captured = {}
    monkeypatch.setattr(app_module, 'sse_broadcast',
                        lambda ev, pl: captured.__setitem__(ev, pl))
    app_module._do_broadcast_pc_state('Kyle')
    payload = captured.get('pc_update')
    assert payload is not None, 'no pc_update broadcast captured'
    d = payload.get('derived')
    assert isinstance(d, dict) and d, 'derived block missing or empty'

    # saves + perception: ints, equal to the @property the Jinja sheet renders
    # (derived deliberately mirrors pc.fort etc., NOT the post-active-effect
    # `effective` values, so a live paint and a hard reload agree to the digit).
    for k in ('fort', 'ref', 'will', 'perception'):
        assert isinstance(d[k], int), f'{k} is not an int'
        assert d[k] == int(getattr(pc, k)), f'derived.{k} drifted from pc.{k}'

    # skills: one row per pc.skills, carrying the display total + an int penalty
    assert isinstance(d['skills'], list) and len(d['skills']) == len(pc.skills)
    by_name = {s['name']: s for s in d['skills']}
    for s in pc.skills:
        row = by_name[s['name']]
        assert row['total'] == s['total']
        assert isinstance(row['penalty'], int)

    # attacks: one card per pc.attacks; each strike carries a label + int mod
    assert isinstance(d['attacks'], list) and len(d['attacks']) == len(pc.attacks)
    for src, a in zip(pc.attacks, d['attacks']):
        assert a['name'] == src['name']
        assert a['damage'] == src['damage']
        assert isinstance(a['is_two_handed'], bool)
        assert len(a['strikes']) == len(src['strikes'])
        for st_src, st in zip(src['strikes'], a['strikes']):
            assert st['label'] == st_src['label']
            assert isinstance(st['mod'], int) and st['mod'] == st_src['mod']

    # the frame is emitted over SSE as JSON — it must serialize cleanly
    json.dumps(payload)


def _fn_block(body, decl, end_anchor):
    """Slice a JS function body out of the rendered sheet, decl → end_anchor."""
    i = body.index(decl)
    j = body.index(end_anchor, i + len(decl))
    return body[i:j]


def test_combat_hot_toggles_paint_in_place(kyle):
    body = app_module.app.test_client().get('/player/sheet/Kyle').data.decode()
    # painters are defined. _paintConditionsMatrix was removed along with the
    # Combat-tab Conditions Matrix card (PF2e sheet de-noise pass) — the top
    # condition-strip and the folded left-rail Quick Conditions editor are
    # now the only two condition-editing surfaces, both painted via
    # _paintMetaQuickConds / applyConditionUpdate instead.
    for fn in ('_paintDerived', '_paintFeatureToggle',
               '_paintSavesBlock', '_paintSkills', '_paintAttacks'):
        assert f'function {fn}' in body, f'{fn} painter missing'
    assert 'function _paintConditionsMatrix' not in body
    # the pc_update handler applies the derived block in place
    assert '_paintDerived(data.derived)' in body

    # the three combat-hot handlers dropped the reload and paint instead
    upd = _fn_block(body, 'async function updateCondition',
                    'window.updateCondition = updateCondition')
    assert 'location.reload' not in upd
    assert '_paintMetaQuickConds(data.conditions' in upd

    feat = _fn_block(body, 'async function toggleFeature', 'async function dailyPrep')
    assert 'location.reload' not in feat
    assert '_paintFeatureToggle(featureName' in feat

    grip = _fn_block(body, 'async function toggleTwoHand', 'function initDailyPrep')
    assert 'location.reload' not in grip


# ── One shared SSE socket per tab (PR #29) ───────────────────────────────
# Every feature subscribes through window.appSSE (the hub in _sse_hub.html)
# instead of opening its own EventSource. These guard against a page
# re-fragmenting back into a second socket, or a standalone page (which can't
# inherit base.html's hub) forgetting to include the hub itself.

def test_only_the_hub_opens_a_raw_sse_socket():
    offenders = []
    for p in sorted(_TEMPLATES.rglob('*.html')):
        if p.name == '_sse_hub.html':
            continue
        if "new EventSource('/api/events')" in p.read_text(encoding='utf-8'):
            offenders.append(p.name)
    assert not offenders, (
        f'raw /api/events sockets outside the shared hub: {offenders} '
        '(subscribe via window.appSSE instead)')


# Partials that call window.appSSE — a standalone page that includes one still
# needs the hub (it can't inherit base.html's).
_APPSSE_PARTIALS = ('_player_nav.html', '_session_curtain.html', '_cosmere_player_nav.html')


def test_standalone_pages_that_use_appsse_define_the_hub():
    missing = []
    for p in sorted(_TEMPLATES.rglob('*.html')):
        txt = p.read_text(encoding='utf-8')
        is_full_page = '<html' in txt and '{% extends' not in txt   # not a base-extending child
        if not is_full_page:
            continue
        needs_hub = 'appSSE' in txt or any(part in txt for part in _APPSSE_PARTIALS)
        defines_hub = '_sse_hub.html' in txt or 'window.appSSE = function' in txt
        if needs_hub and not defines_hub:
            missing.append(p.name)
    assert not missing, (
        f'standalone pages need the SSE hub but never define it: {missing} '
        '(add {% include "_sse_hub.html" %} in <head>)')


# SSE reliability: the hub recovers a silently-dead socket on wake, and the live
# surfaces refetch on (re)connect so events missed while asleep are reconciled.
def test_sse_hub_recovers_on_wake():
    hub = (_TEMPLATES / '_sse_hub.html').read_text()
    assert 'visibilitychange' in hub and 'function wake' in hub, \
        '_sse_hub.html must force-reconnect on wake (a slept socket never fires onerror)'
    assert "addEventListener('online'" in hub and "addEventListener('pageshow'" in hub


def test_live_surfaces_refetch_on_reconnect():
    tracker = (_TEMPLATES / 'tracker.html').read_text()
    assert "appSSE('connected'" in tracker and '/api/tracker_state' in tracker, \
        'tracker must refetch state on (re)connect'
    sheet = (_TEMPLATES / 'cosmere_sheet.html').read_text()
    assert "addEventListener('connected'" in sheet, \
        'Cosmere sheet must resync its combat strip on (re)connect'
