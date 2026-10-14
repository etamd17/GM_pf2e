"""The UI-02b sheet preserves the pre-tab render and makes every section reachable."""
from collections import Counter
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
import re

import pytest
import app


class Sheet(HTMLParser):
    """Small DOM index; includes ancestors so a visible target cannot hide in a tab."""
    def __init__(self, html):
        super().__init__()
        self.nodes, self.stack = [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = dict(attrs, tag=tag, ancestors=tuple(self.stack))
        self.nodes.append(node)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                       'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1]['tag'] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]['tag'] == tag:
                del self.stack[i:]
                break

    def find(self, **attrs):
        return [n for n in self.nodes if all(n.get(k) == v for k, v in attrs.items())]


# Frozen before changing the template, from origin/main 3b021789.
# Complete original order, including base/glyph ids; layout may reorder, never remove.
# Actual DOM contract: 122 ids, 86 onclick handlers. Raw text counts (124/63) also
# include JavaScript strings and omit handlers using single HTML quotes.
BASELINE_IDS = ('cg-order-windrunners',
 'cg-order-skybreakers',
 'cg-order-dustbringers',
 'cg-order-edgedancers',
 'cg-order-truthwatchers',
 'cg-order-lightweavers',
 'cg-order-elsecallers',
 'cg-order-willshapers',
 'cg-order-stonewards',
 'cg-order-bondsmiths',
 'cg-surge-adh',
 'cg-surge-grv',
 'cg-surge-dvs',
 'cg-surge-abr',
 'cg-surge-prg',
 'cg-surge-ill',
 'cg-surge-trs',
 'cg-surge-trp',
 'cg-surge-chs',
 'cg-surge-tsn',
 'cg-metal-iron',
 'cg-metal-steel',
 'cg-metal-tin',
 'cg-metal-pewter',
 'cg-metal-zinc',
 'cg-metal-brass',
 'cg-metal-copper',
 'cg-metal-bronze',
 'cg-metal-aluminum',
 'cg-metal-duralumin',
 'cg-metal-chromium',
 'cg-metal-nicrosil',
 'cg-metal-gold',
 'cg-metal-electrum',
 'cg-metal-cadmium',
 'cg-metal-bendalloy',
 'cs-vbar',
 'vb-health',
 'vb-investiture',
 'vb-focus',
 'vb-cond',
 'cos-combat',
 'cc-round',
 'cc-turn',
 'cc-controls',
 'cond-fold-btn',
 'cos-conditions',
 'cond-none',
 'cos-cond-fx',
 'cos-goals',
 'cs-deflect',
 'cs-deflect-plate',
 'cs-deflect-n',
 'cs-deflect-types',
 'orb-health',
 'fill-health',
 'arc-health',
 'rv-health',
 'rv-injuries',
 'orb-focus',
 'fill-focus',
 'arc-focus',
 'rv-focus',
 'orb-investiture',
 'fill-investiture',
 'arc-investiture',
 'rv-investiture',
 'btn-forsake',
 'cos-sl-actions',
 'btn-enhance',
 'btn-shardblade',
 'btn-shardplate',
 'enh-chip',
 'cos-squire',
 'cs-surge-toggle',
 't-adv',
 't-dis',
 't-plot',
 'cs-result',
 'cs-skills-grid',
 'cos-shardblade-strike',
 'cos-wallet',
 'cos-wallet-spheres',
 'cos-wallet-goods',
 'cos-crafted-list',
 'cos-workshop',
 'ws-effect',
 'ws-body',
 'pc-notes',
 'pc-notes-status',
 'gm-toast-container',
 'pf2e-action-toast-container',
 'dice-widget',
 'dice-panel',
 'dice-total',
 'dice-breakdown',
 'custom-roll-form',
 'custom-roll-input',
 'screen-flash',
 'init-flourish',
 'boss-reveal',
 'br-epithet',
 'br-name',
 'br-level',
 'round-event-banner',
 'reb-round',
 'reb-title',
 'reb-text',
 'scene-mood',
 'qs-overlay',
 'qs-dialog',
 'qs-input',
 'qs-cats',
 'qs-results',
 'chat-widget',
 'chat-panel',
 'chat-messages',
 'chat-form',
 'chat-input',
 'chat-fab',
 'chat-unread',
 'kb-help-card')
BASELINE_ONCLICKS = ('window._qsOpen && window._qsOpen()',
 'window.print()',
 'condFoldToggle()',
 'cosCond("afflicted")',
 'cosCond("determined")',
 'cosCond("disoriented")',
 'cosCond("empowered")',
 'cosCond("enhanced")',
 'cosCond("exhausted")',
 'cosCond("focused")',
 'cosCond("immobilized")',
 'cosCond("prone")',
 'cosCond("restrained")',
 'cosCond("slowed")',
 'cosCond("stunned")',
 'cosCond("surprised")',
 'cosCond("unconscious")',
 'cosGoalAdd()',
 'orbToggle(this)',
 "cosApply('health',-1,this)",
 "cosApply('health',1,this)",
 "cosAdjust('injuries',-1)",
 "cosAdjust('injuries',1)",
 'orbToggle(this)',
 "cosApply('focus',-1,this)",
 "cosApply('focus',1,this)",
 'orbToggle(this)',
 "cosApply('investiture',-1,this)",
 "cosApply('investiture',1,this)",
 'cosBreathe()',
 "cosRest('short')",
 "cosRest('long')",
 'cosForsake()',
 'cosBreathe()',
 'cosEnhance()',
 'cosRegenerate()',
 'cosMendInjury()',
 'cosShardblade()',
 'cosShardplate()',
 'cosSurge("Adhesion", 1, "To perform a Full Lashing, spend 1 Investiture or more, infusing it '
 'into two unsecured objects within your reach and within 5 feet of each other; you must have a '
 'hand free and touch the targets.")',
 'cosSurge("Gravitation", 1, "To perform a Basic Lashing, spend 1 Investiture or more to infuse a '
 'character or object within your reach; you must have a hand free and touch the target.")',
 'toggleLockedSurges(this)',
 "cosTog('adv')",
 "cosTog('dis')",
 "cosTog('plot')",
 "cosVis('group')",
 "cosVis('gm')",
 "cosVis('private')",
 'cosRoll("Agility", 3, "spd")',
 'cosRoll("Athletics", 6, "str")',
 'cosRoll("Crafting", 0, "int")',
 'cosRoll("Deception", 0, "pre")',
 'cosRoll("Deduction", 0, "int")',
 'cosRoll("Discipline", 2, "wil")',
 'cosRoll("Heavy Weaponry", 6, "str")',
 'cosRoll("Intimidation", 2, "wil")',
 'cosRoll("Insight", 0, "awa")',
 'cosRoll("Leadership", 0, "pre")',
 'cosRoll("Lore", 0, "int")',
 'cosRoll("Light Weaponry", 3, "spd")',
 'cosRoll("Medicine", 0, "int")',
 'cosRoll("Perception", 0, "awa")',
 'cosRoll("Persuasion", 0, "pre")',
 'cosRoll("Stealth", 3, "spd")',
 'cosRoll("Survival", 0, "awa")',
 'cosRoll("Thievery", 3, "spd")',
 'cosRoll("Adhesion", 0, "pre")',
 'cosRoll("Gravitation", 0, "awa")',
 'cosStrike("Shardblade", 6, "2d8", "spirit")',
 'cosAbility("Breathe Stormlight", "Draw Stormlight from infused spheres within 5 feet to recover '
 'Investiture up to your maximum.")',
 'cosAbility("Enhance", "Spend 1 Investiture to become Enhanced [Strength +1] and Enhanced [Speed '
 '+1] until the end of your next turn.")',
 'cosAbility("Regenerate", "Spend 1 Investiture to recover health equal to 1d6 + your current '
 'tier.")',
 'toggleDicePanel()',
 'quickRoll(4)',
 'quickRoll(6)',
 'quickRoll(8)',
 'quickRoll(10)',
 'quickRoll(12)',
 'quickRoll(20)',
 'quickRoll(100)',
 'rollPlotDie()',
 'toggleDicePanel()',
 'toggleChatPanel()',
 'toggleChatPanel()',
 'event.stopPropagation()',
 "document.getElementById('kb-help-card').classList.add('hidden'); "
 "document.getElementById('kb-help-card').style.display='none';")
BASELINE_LITERAL_COUNTS = (124, 63)
BUILD = {'attributes': {'spd': 3, 'str': 3, 'wil': 2},
 'connections': 'Bridge Four',
 'ideals_sworn': 4,
 'level': 12,
 'name': 'Kal',
 'path': 'warrior',
 'radiant_order': 'windrunners',
 'skills': {'ath': 3, 'hwp': 3},
 'talents': [{'id': 'radiant:Take Squire (Windrunner)', 'name': 'Take Squire (Windrunner)'},
             {'id': 'radiant:Wound Regeneration', 'name': 'Wound Regeneration'}]}


@pytest.fixture
def render_sheet(monkeypatch):
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    monkeypatch.setattr(app, '_account_mode', lambda: False)
    monkeypatch.setattr(app, '_cosmere_world', lambda: 'stormlight')

    def render(radiant=True, readonly=False):
        build = deepcopy(BUILD)
        if not radiant:
            build.update(radiant_order='', ideals_sworn=0, talents=[])
        doc = {'id': 'ab' * 16, 'system': 'cosmere', 'name': 'Kal',
               'owner_user_id': 'u1', 'build': build}
        monkeypatch.setattr(app, '_cosmere_can_act_on', lambda doc: not readonly)
        with app.app.test_request_context('/cosmere/pc/' + doc['id']):
            return app._render_cosmere_pc_sheet(doc)
    return render


def test_main_render_ids_and_handlers_are_preserved(render_sheet):
    html = render_sheet()
    dom = Sheet(html)
    ids = [n['id'] for n in dom.nodes if 'id' in n]
    assert Counter(ids) == Counter(BASELINE_IDS)
    assert len(ids) == len(set(ids)), 'duplicate ids break live updates'
    assert Counter(n['onclick'] for n in dom.nodes if 'onclick' in n) == Counter(BASELINE_ONCLICKS)
    assert (html.count('id="'), html.count('onclick="')) == BASELINE_LITERAL_COUNTS


@pytest.mark.parametrize('radiant', [True, False])
@pytest.mark.parametrize('readonly', [True, False])
def test_tabs_and_default_panel(render_sheet, radiant, readonly):
    dom = Sheet(render_sheet(radiant, readonly))
    assert len(dom.find(role='tablist')) == 1
    expected = ['play', 'radiant', 'gear', 'character'] if radiant else ['play', 'gear', 'character']
    tabs, panels = dom.find(role='tab'), dom.find(role='tabpanel')
    assert [n['data-tab'] for n in tabs] == expected
    assert [n['data-panel'] for n in panels] == expected
    for tab, panel in zip(tabs, panels):
        active = tab['data-tab'] == 'play'
        assert tab['type'] == 'button'
        assert tab['aria-selected'] == str(active).lower()
        assert tab['tabindex'] == ('0' if active else '-1')
        assert ('hidden' not in panel) == active
    for target in ['orb-health', 'orb-focus', 'cs-deflect', 'cos-conditions']:
        for node in dom.find(id=target):
            assert not any(a.get('role') == 'tabpanel' for a in node['ancestors'])
    for target, panel in [('cs-skills-grid', 'play'), ('cos-wallet', 'gear'),
                          ('cos-workshop', 'gear'), ('cos-goals', 'character'),
                          ('pc-notes', 'character'), ('cos-sl-actions', 'radiant')]:
        for node in dom.find(id=target):
            assert any(a.get('data-panel') == panel for a in node['ancestors']), target


def test_non_pc_actor_has_no_radiant_tab(monkeypatch):
    monkeypatch.setattr(app, '_cosmere_doc_by_id', lambda _: {
        '_id': 'guard', 'name': 'Guard', 'type': 'adversary', 'system': {}})
    with app.app.test_request_context('/cosmere/sheet/guard'):
        dom = Sheet(app.cosmere_sheet('guard'))
    assert [n['data-tab'] for n in dom.find(role='tab')] == ['play', 'gear', 'character']
    assert not dom.find(**{'data-panel': 'radiant'})


def test_hidden_print_keyboard_and_storage_safeguards():
    src = (Path(__file__).resolve().parents[1] / 'templates/cosmere_sheet.html').read_text(encoding='utf-8')
    assert re.search(r'\.cs-panel\[hidden\]\s*\{\s*display:\s*none', src)
    assert re.search(r'@media print\s*\{[^}]*\.cs-panel\[hidden\][^{]*\{\s*display:\s*block\s*!important', src)
    assert re.search(r'\.cs-tabs\s*\{\s*display:\s*none\s*!important', src)
    for token in ('ArrowLeft', 'ArrowRight', 'Home', 'End', 'aria-controls', 'aria-labelledby',
                  'panel.id', 'tab.id', 'tab.tabIndex', 'panel.hidden', 'tab.focus()'):
        assert token in src
    assert re.search(r"'cosmere-sheet-tab:'\s*\+\s*.*actor_id", src)
    assert re.search(r'try\s*\{[^}]*sessionStorage.getItem', src)
    assert re.search(r'try\s*\{[^}]*sessionStorage.setItem', src)


@pytest.mark.parametrize('radiant', [True, False])
def test_infected_arts_remain_reachable(render_sheet, monkeypatch, radiant):
    original_render = app.render_template
    def with_infected(template, **context):
        context['infected'] = [{'disease': 'Test infection', 'art': 'Test art',
                                'cost': 'Test cost', 'abilities': []}]
        return original_render(template, **context)
    monkeypatch.setattr(app, 'render_template', with_infected)
    dom = Sheet(render_sheet(radiant))
    art, = dom.find(style='border-color:rgba(205,81,191,.5);')
    assert any(a.get('data-panel') == ('radiant' if radiant else 'character') for a in art['ancestors'])


def test_foldable_sections_cannot_swallow_goals(render_sheet):
    dom = Sheet(render_sheet())
    goal, = dom.find(id='cos-goals')
    assert not any(a['tag'] == 'section' for a in goal['ancestors'])
    for heading in dom.find(**{'class': 'cs-section-h cs-foldable'}):
        # Each old foldable section gets its own sibling boundary. Its existing
        # nextElementSibling loop cannot absorb a tab panel or the goals block.
        assert heading['ancestors'][-1]['tag'] == 'section'


@pytest.mark.parametrize('radiant,saved,blocked', [
    (True, None, False), (True, 'gear', False), (True, 'radiant', False),
    (False, 'radiant', False), (True, 'missing', False), (True, 'gear', True)])
def test_tab_controller_runtime(render_sheet, radiant, saved, blocked):
    import json
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for the tab controller behavior test')
    html = render_sheet(radiant)
    code = re.search(r'<script data-cs-tabs>(.*?)</script>', html, re.S).group(1)
    harness = r'''
const vm = require('node:vm'), assert = require('node:assert/strict');
let input = ''; process.stdin.on('data', d => input += d);
process.stdin.on('end', () => {
  const {code, names, saved, blocked} = JSON.parse(input);
  let focused = null;
  const writes = [];
  function element(dataset) {
    return {dataset, attrs: {}, listeners: {}, hidden: false,
      setAttribute(k,v) { this.attrs[k] = v; },
      addEventListener(k,f) { this.listeners[k] = f; },
      focus() { focused = this; }};
  }
  const tabs = names.map(n => element({tab:n}));
  const panels = names.map(n => element({panel:n}));
  const context = {document:{querySelectorAll(s){return s === '.cs-tab' ? tabs : panels;}},
    sessionStorage:{
      getItem(key){ assert.equal(key, 'cosmere-sheet-tab:' + 'ab'.repeat(16)); if(blocked) throw Error('denied'); return saved; },
      setItem(key,value){ if(blocked) throw Error('denied'); writes.push([key,value]); }
    }};
  vm.runInNewContext(code, context);
  function selected(name) {
    tabs.forEach((t,i) => {
      assert.equal(t.attrs['aria-controls'], panels[i].id);
      assert.equal(panels[i].attrs['aria-labelledby'], t.id);
      assert.equal(t.attrs['aria-selected'], String(t.dataset.tab === name));
      assert.equal(t.tabIndex, t.dataset.tab === name ? 0 : -1);
      assert.equal(panels[i].hidden, t.dataset.tab !== name);
    });
  }
  selected(!blocked && names.includes(saved) ? saved : 'play');
  tabs.forEach(t => { t.listeners.click(); selected(t.dataset.tab); });
  function key(index, key, expected) {
    let prevented = false;
    tabs[index].listeners.keydown({key,preventDefault(){prevented=true;}});
    assert.equal(prevented, true); selected(names[expected]); assert.equal(focused,tabs[expected]);
  }
  key(0,'ArrowLeft',names.length-1);
  key(names.length-1,'ArrowRight',0);
  key(0,'End',names.length-1);
  key(names.length-1,'Home',0);
  key(0,'ArrowRight',1);
  key(1,'ArrowLeft',0);
  tabs[0].listeners.keydown({key:'Tab',preventDefault(){throw Error('Tab must keep native behavior');}});
  selected('play');
  if(!blocked) assert.deepEqual(writes.at(-1),['cosmere-sheet-tab:'+'ab'.repeat(16),'play']);
  console.log('tab behavior passed');
});
'''
    result = subprocess.run([node, '-e', harness], input=json.dumps({
        'code': code, 'names': ['play', 'radiant', 'gear', 'character'] if radiant else ['play', 'gear', 'character'],
        'saved': saved, 'blocked': blocked}), capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'tab behavior passed'
