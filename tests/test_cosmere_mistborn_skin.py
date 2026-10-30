"""World-specific sheet chrome and safe condition-tooltip event targets."""
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess

import pytest
import app


ROOT = Path(__file__).resolve().parents[1]


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.nodes = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.nodes.append(dict(attrs, tag=tag))


@pytest.mark.parametrize('world', ['mistborn', 'stormlight'])
@pytest.mark.parametrize('carried_fabrial', [False, True])
def test_sheet_flavour_and_navigation_follow_world_without_changing_registry(monkeypatch, world, carried_fabrial):
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    monkeypatch.setattr(app, '_account_mode', lambda: False)
    monkeypatch.setattr(app, '_cosmere_world', lambda: world)
    monkeypatch.setattr(app, '_active_system', lambda: 'cosmere')
    monkeypatch.setattr(app, '_cosmere_can_act_on', lambda _: True)
    config = dict(app.CAMPAIGN_DEFAULT, cosmere_world=world)
    monkeypatch.setattr(app, 'CAMPAIGN_DEFAULT', config)
    registry_ui = app._active_system_ui()
    registry_before = deepcopy(registry_ui)
    doc = {'id': 'ab' * 16, 'system': 'cosmere', 'name': 'Vin',
           'owner_user_id': None, 'build': {'name': 'Vin', 'path': 'warrior',
           'level': 1, 'attributes': {'str': 2, 'spd': 2, 'int': 2, 'wil': 2,
                                    'awa': 2, 'pre': 2}}}
    if carried_fabrial:
        doc['play_state'] = {'crafted_fabrials': [{
            'key': 'saved-device', 'name': 'Saved device', 'charges': 3, 'current': 2,
            'effect': '', 'upgrades': [], 'drawbacks': []}]}
    original = deepcopy(doc)
    with app.app.test_request_context('/cosmere/pc/' + doc['id']):
        html = app._render_cosmere_pc_sheet(doc)
    dom = Elements(html)
    ids = {node.get('id') for node in dom.nodes}
    titles = {node.get('href'): node.get('title') for node in dom.nodes
              if node['tag'] == 'a'}
    if world == 'mistborn':
        assert 'No Investiture pool.' in html
        assert 'title="Not a Radiant"' not in html
        assert 'e.g. equipment, a companion or patron, a new position' in html
        assert 'Swear a First Ideal to gain Stormlight.' not in html
        assert 'cos-workshop' not in ids and 'ws-effect' not in ids
        assert titles['/cosmere/generator'] == 'Scadrial names, NPCs, weather, loot'
        assert titles['/cosmere/loot'] == 'Boxings, clips & spoils ledger'
    else:
        assert 'Swear a First Ideal to gain Stormlight.' in html
        assert 'title="Not a Radiant"' in html
        assert 'e.g. a fabrial or Shardplate, a companion or patron, a noble title' in html
        assert 'cos-workshop' in ids and 'ws-effect' in ids
        assert titles['/cosmere/generator'] == 'Rosharan names, NPCs, weather, loot'
        assert titles['/cosmere/loot'] == 'Spheres & spoils ledger'
    has_fabrial_section = world == 'stormlight' or carried_fabrial
    assert ('>Fabrials <span' in html) == has_fabrial_section
    assert ('cos-crafted-list' in ids) == has_fabrial_section
    if carried_fabrial:
        assert 'saved-device' in html and 'Saved device' in html
    assert doc == original
    assert registry_ui == registry_before == app._active_system_ui()


@pytest.mark.parametrize('template', ['base.html', '_player_nav.html'])
def test_condition_tooltip_accepts_text_and_element_targets(template):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    src = (ROOT / 'templates' / template).read_text(encoding='utf-8')
    helper = re.search(r'function _findPill\(target\) \{.*?\n\s*\}', src, re.S).group()
    script = helper + r'''
const assert = require('node:assert/strict');
const pill = {};
const selector = '.condition-pill[data-cond-name], .condition-toggle[data-cond-name], .cond-chip[data-cond-name], .cp[data-cond-name]';
const element = {closest(s) {assert.equal(s, selector); return pill;}};
assert.equal(_findPill(element), pill);
assert.equal(_findPill({nodeType: 3, parentElement: element}), pill);
assert.equal(_findPill({closest() {return null;}}), null);
for (const target of [null, undefined, {}, {nodeType: 9}, {parentElement: {}}, {parentElement: null}]) {
    assert.equal(_findPill(target), null);
}
'''
    result = subprocess.run([node, '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
