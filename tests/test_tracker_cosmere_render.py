"""Regression: the Cosmere tracker must not crash on render.

The PF2e XP/difficulty bar (#xp-bar-wrap) is only emitted when active_system
!= 'cosmere'. renderMeta() reads that element and, in its else branch, called
xpWrap.classList.add('hidden') unconditionally -- so on a COSMERE tracker the
element is absent, getElementById returns null, and EVERY render threw
("can't access property classList, xpWrap is null"). Because apiPost swallows
render errors, HP/condition changes applied on the server but never repainted:
the Cosmere tracker looked frozen ("can't adjust HP at all").

This pins the two coupled facts so neither can drift back into a null-deref:
  1. the Cosmere tracker really does omit #xp-bar-wrap, and
  2. renderMeta guards the null before touching .classList.

Subprocess + throwaway DATA_DIR, mirroring tests/test_cosmere_campaign_binding.py.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TRACKER_TEMPLATE = Path(_REPO, 'templates', 'tracker.html')


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_cosmere_tracker_renders_without_xp_bar_nulldef():
    r = _run('''
        import tempfile, os, re
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        c = A.app.test_client()
        assert c.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'}).status_code == 302
        assert c.post('/campaigns/new', data={'name': 'Roshar', 'system': 'cosmere'}).status_code == 302
        cid = [i for i in storage.list_campaign_ids() if campaigns.get_campaign(i)['name'] == 'Roshar'][0]
        assert c.post('/campaign/' + cid + '/activate').status_code == 302

        page = c.get('/tracker')
        assert page.status_code == 200
        html = page.data.decode()
        # Cosmere mode omits the PF2e XP/difficulty bar entirely...
        assert 'id="xp-bar-wrap"' not in html, 'cosmere tracker unexpectedly emits #xp-bar-wrap'
        # ...so renderMeta MUST guard the (now-null) element before .classList.
        assert "else if (xpWrap)" in html, 'renderMeta lost its null guard -> cosmere render will throw'
        # and must NOT contain the old unguarded else that dereferenced a null xpWrap
        assert not re.search(r"\\}\\s*else\\s*\\{\\s*\\n\\s*xpWrap\\.classList", html), 'unguarded xpWrap.classList in else branch'
        # PF2e-only page controls are omitted from a Cosmere campaign rather
        # than merely hidden after the client script starts.
        for marker in ('id="multi-save-type"', 'id="multi-save-dc"',
                       'id="multi-cond-val"', 'id="multi-cond-rounds"',
                       'id="loot-panel"', 'id="check-request-panel"',
                       'id="custom-monster-form"'):
            assert marker not in html, marker
        print('COSMERE_TRACKER_RENDER_OK')
    ''')
    assert 'COSMERE_TRACKER_RENDER_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)


def test_pf2e_tracker_still_emits_xp_bar():
    """The PF2e tracker keeps its XP bar (the guard change is null-safe, not a removal)."""
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        c = A.app.test_client()
        assert c.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'}).status_code == 302
        assert c.post('/campaigns/new', data={'name': 'Golarion', 'system': 'pf2e'}).status_code == 302
        cid = [i for i in storage.list_campaign_ids() if campaigns.get_campaign(i)['name'] == 'Golarion'][0]
        assert c.post('/campaign/' + cid + '/activate').status_code == 302
        html = c.get('/tracker').data.decode()
        assert 'id="xp-bar-wrap"' in html, 'pf2e tracker should still render the XP/difficulty bar'
        for marker in ('id="multi-save-type"', 'id="multi-save-dc"',
                       'id="multi-cond-val"', 'id="multi-cond-rounds"',
                       'id="loot-panel"', 'id="check-request-panel"',
                       'id="custom-monster-form"'):
            assert marker in html, marker
        print('PF2E_TRACKER_XPBAR_OK')
    ''')
    assert 'PF2E_TRACKER_XPBAR_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)


def _tracker_renderer_source():
    """Return the real client row and inspector renderers without Jinja tags."""
    source = _TRACKER_TEMPLATE.read_text(encoding='utf-8')
    row = 'function renderInitList()' + source.split('function renderInitList()', 1)[1].split(
        '// Cosmere stat boxes for the hero card', 1)[0]
    boxes = 'function cosmereStatBoxes' + source.split('function cosmereStatBoxes', 1)[1].split(
        '// UX-C: one inspector.', 1)[0]
    panel = 'let _inspectId' + source.split('let _inspectId', 1)[1].split(
        '/* Vault-note resolution cache', 1)[0]
    code = row + '\n' + boxes + '\n' + panel
    return re.sub(r'^\s*\{%.*?%\}\s*$', '', code, flags=re.M)


def test_client_renderers_keep_pf2e_controls_out_of_cosmere_rows_and_inspector():
    """Exercise the real row and inspector renderers for both game systems."""
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for the tracker renderer behavior test')
    harness = r'''
const assert = require('node:assert/strict');
let input = ''; process.stdin.on('data', d => input += d);
process.stdin.on('end', () => {
  const {code} = JSON.parse(input);
  const prelude = String.raw`
    function classList(){ const s=new Set(); return {add(...v){v.forEach(x=>s.add(x));},remove(...v){v.forEach(x=>s.delete(x));},contains(v){return s.has(v);}}; }
    function element(){ return {innerHTML:'',textContent:'',classList:classList(),style:{},querySelector(){return null;}}; }
    const elements = {'initiative-list':element(),'empty-state':element(),'active-panel-wrap':element(),'main-layout':element(),'ap-name':element(),'ap-subtitle':element(),'ap-body':element()};
    const document = {activeElement:null,getElementById(id){return elements[id] || (elements[id]=element());},querySelector(){return null;},querySelectorAll(){return [];}};
    const COSMERE_INIT_MODE='traditional';
    const CONDITIONS_LIST=['frightened','off_guard','prone'];
    const COSMERE_CONDITIONS=['determined','prone','slowed'];
    const QUICK_CONDS=[{k:'off_guard',l:'Off-guard'},{k:'frightened',l:'Frightened'}];
    const VALUED_CONDITIONS=['frightened','dying','wounded','doomed','slowed'];
    const DEADLY_CONDS=new Set(['dying','wounded','doomed']);
    const DAMAGE_TYPES=['untyped','fire']; const COSMERE_DAMAGE_TYPES=['impact','keen'];
    function damageTypeListFor(c){return c && c.system==='cosmere'?COSMERE_DAMAGE_TYPES:DAMAGE_TYPES;}
    function condTooltip(cond){return cond==='prone'?'AC -2 circumstance. Melee -2. Standing costs 1 action.':cond;}
    function conditionTooltipFor(c,cond){return c && c.system==='cosmere'?cond.replace(/_/g,' '):condTooltip(cond);}
    function condChipClass(){return '';}
    function classRuneId(){return '#x';}
    function initials(name){return name.slice(0,2).toUpperCase();}
    function dispName(c){return c.name;}
    function heroFlamesHtml(){return '';}
    function investitureSphereHtml(){return '';}
    function hpTextClass(){return '';}
    function hpPctColor(){return '#fff';}
    function hpBarClass(){return '';}
    function escapeHtml(s){return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
    function costPips(){return '';}
    function initSortable(){} function _restoreMultiSelection(){} function _decorateActiveNoteLink(){}
    let STATE={combatants:[],turn_index:0,round:2};
  `;
  const suffix = String.raw`
    function actor(system,isPC){
      return {instance_id:system+(isPC?'-pc':'-npc'),name:isPC?'Test Hero':'Test Blade',system,is_pc:isPC,is_hazard:false,
        initiative:18,current_hp:18,max_hp:24,level:3,ac:18,fort:8,ref:7,will:6,
        perception:7,speed:25,conditions:{prone:true,frightened:2,dying:1,wounded:1},
        condition_expiry:{},persistent_damage:'1d6 fire',delaying:false,reaction_used:false,
        max_actions:3,actions_used:0,elite_weak:1,visible_to_players:true,epithet:'',traits:[],
        immunities:[],resistances:[],weaknesses:[],strikes:[{name:'Glass Blade',hit:'+10',damage:'1d8 keen'}],
        actions:[],feats:[{name:'PF2e Feat',desc:''}],spell_casters:[{name:'PF2e Spells',levels:[]}],
        spell_attack:0,spell_dc:0,focus_pool:0,class_name:'',ancestry:'',subclass:'',creature_type:'',
        cosmere:system==='cosmere'?{role:'Rival',tier:2,injuries:0,defenses:{phy:16,cog:14,spi:13},deflect:{value:2},focus_max:3,investiture_max:2,attributes:{str:2,spd:3,int:1,wil:2,awa:2,pre:1}}:null};
    }
    function render(system){
      const npc=actor(system,false), pc=actor(system,true); STATE.combatants=[npc,pc]; STATE.turn_index=0;
      renderInitList(); renderActivePanel(); const npcPanel=elements['ap-body'].innerHTML;
      STATE.turn_index=1; renderActivePanel();
      return {row:elements['initiative-list'].innerHTML,panel:npcPanel+elements['ap-body'].innerHTML};
    }
    return {cosmere:render('cosmere'),pf2e:render('pf2e')};
  `;
  const out = new Function(prelude + code + suffix)();
  const cos = out.cosmere.row + out.cosmere.panel;
  const pf = out.pf2e.row + out.pf2e.panel;
  for (const leak of ['MAP','M2 ','agile','class="cq-add"','title="Elite"','(Elite)',
                      'class="pd-block"','DC 15','Frightened decreases','Recovery flat check',
                      'Recall Knowledge','AC -2 circumstance','data-cond-name="frightened"',
                      'data-cond-name="dying"','PF2e Feat','PF2e Spells']) {
    assert.equal(cos.includes(leak), false, 'Cosmere leak: ' + leak);
  }
  assert.match(cos, /Glass Blade/);
  assert.match(cos, /TIER 2/);
  for (const kept of ['MAP','M2 ','agile','class="cq-add"','title="Elite"','(Elite)',
                      'class="pd-block"','DC 15','Frightened decreases','Recovery flat check',
                      'Recall Knowledge','AC -2 circumstance','PF2e Feat','PF2e Spells']) {
    assert.equal(pf.includes(kept), true, 'PF2e control missing: ' + kept);
  }
  console.log('tracker renderers passed');
});
'''
    result = subprocess.run(
        [node, '-e', harness], input=json.dumps({'code': _tracker_renderer_source()}),
        capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'tracker renderers passed'


def test_multi_picker_vocabularies_follow_campaign_not_combatant_mix():
    """Page-level Multi controls stay bound to the active campaign system."""
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for the tracker Multi behavior test')
    source = _TRACKER_TEMPLATE.read_text(encoding='utf-8')
    sync = 'function syncMultiDamageTypes()' + source.split(
        'function syncMultiDamageTypes()', 1)[1].split(
            '// Keep the active combatant visible', 1)[0]
    populate = 'function _populateMultiCondSel()' + source.split(
        'function _populateMultiCondSel()', 1)[1].split('function toggleMultiMode()', 1)[0]
    condition_list = 'function _reConditionList()' + source.split(
        'function _reConditionList()', 1)[1].split('function _reNextFireRound', 1)[0]
    harness = r'''
const assert = require('node:assert/strict');
let input = ''; process.stdin.on('data', d => input += d);
process.stdin.on('end', () => {
  const {code} = JSON.parse(input);
  let campaign = 'cosmere';
  const damage = {dataset:{},value:'',innerHTML:''};
  const conditions = {options:[],appendChild(o){this.options.push(o);}};
  const document = {
    body:{classList:{contains(cls){return cls === 'system-' + campaign;}}},
    getElementById(id){return id === 'multi-dmg-type' ? damage : conditions;},
    createElement(){return {};}
  };
  const DAMAGE_TYPES=['untyped','fire'];
  const COSMERE_DAMAGE_TYPES=['impact','keen'];
  const CONDITIONS_LIST=['frightened','off_guard'];
  const COSMERE_CONDITIONS=['determined','prone'];
  const STATE={combatants:[{system:'pf2e'}]};
  new Function('document','DAMAGE_TYPES','COSMERE_DAMAGE_TYPES','CONDITIONS_LIST',
    'COSMERE_CONDITIONS','STATE', code + `
      syncMultiDamageTypes(); _populateMultiCondSel();
    `)(document,DAMAGE_TYPES,COSMERE_DAMAGE_TYPES,CONDITIONS_LIST,COSMERE_CONDITIONS,STATE);
  assert.equal(damage.dataset.sys, 'cosmere');
  assert.match(damage.innerHTML, /impact/);
  assert.deepEqual(conditions.options.map(o=>o.value), COSMERE_CONDITIONS);

  campaign = 'pf2e';
  STATE.combatants = [{system:'cosmere'}];
  damage.dataset = {}; damage.value = ''; damage.innerHTML = '';
  conditions.options = [];
  new Function('document','DAMAGE_TYPES','COSMERE_DAMAGE_TYPES','CONDITIONS_LIST',
    'COSMERE_CONDITIONS','STATE', code + `
      syncMultiDamageTypes(); _populateMultiCondSel();
    `)(document,DAMAGE_TYPES,COSMERE_DAMAGE_TYPES,CONDITIONS_LIST,COSMERE_CONDITIONS,STATE);
  assert.equal(damage.dataset.sys, 'pf2e');
  assert.match(damage.innerHTML, /untyped/);
  assert.deepEqual(conditions.options.map(o=>o.value), CONDITIONS_LIST);
  console.log('campaign vocabularies passed');
});
'''
    code = sync + '\n' + populate + '\n' + condition_list
    result = subprocess.run(
        [node, '-e', harness], input=json.dumps({'code': code}), capture_output=True,
        text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'campaign vocabularies passed'


def test_pf2e_tracker_routes_reject_cosmere_combatants_without_mutating_them():
    r = _run('''
        import os, tempfile
        from types import SimpleNamespace
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A

        class CosmereTarget(SimpleNamespace):
            def apply_elite_weak(self, value):
                self.elite_weak = value

        target = CosmereTarget(instance_id='cos-1', name='Steel Inquisitor', system='cosmere',
            is_pc=False, conditions={'prone': False, 'dying': 1}, condition_expiry={},
            persistent_damage='', elite_weak=0, fort=8, ref=7, will=6, level=5,
            visible_to_players=True, traits=[], ac=20, strikes=[], actions=[])
        A.ACTIVE_ENCOUNTER[:] = [target]
        A._persist_encounter_state = lambda: None
        A._broadcast_encounter_state = lambda: None
        A._combat_log = lambda *a, **k: None
        A._bump_campaign_stat = lambda *a, **k: None
        c = A.app.test_client()

        checks = [
            c.post('/api/toggle_condition/cos-1', data={'condition':'prone','action':'add'}),
            c.post('/api/set_persistent_damage/cos-1', data={'persistent_damage':'1d6 fire'}),
            c.post('/api/toggle_elite_weak/cos-1', data={'mode':'elite'}),
            c.post('/api/multi_save_damage', json={'ids':['cos-1'],'save':'reflex','dc':15,'damage':8}),
            c.post('/api/recovery_check/cos-1', json={'d20':10}),
            c.get('/api/recall_knowledge_info/cos-1'),
            c.post('/api/recall_knowledge/cos-1', json={'pc_name':''}),
        ]
        for response in checks:
            assert response.status_code == 400, (response.request.path, response.status_code, response.data)
            body = response.get_json() or {}
            assert 'PF2e' in body.get('error', ''), (response.request.path, body)
        assert target.conditions == {'prone': False, 'dying': 1}
        assert target.persistent_damage == ''
        assert target.elite_weak == 0
        print('COSMERE_PF2E_ROUTES_REJECTED')
    ''')
    assert 'COSMERE_PF2E_ROUTES_REJECTED' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)


def test_cosmere_full_stats_payload_uses_cosmere_fields_for_pc_and_adversary():
    """The shared stat endpoint must not dereference PF2e-only PC fields."""
    r = _run('''
        import os, tempfile
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from systems.cosmere.actor import CosmereActor

        def actor(name, actor_type):
            out = CosmereActor({'name': name, 'system': 'cosmere', 'system_data': {
                'name': name, 'type': actor_type, 'system': {
                    'level': {'value': 4}, 'tier': 2, 'role': 'rival',
                    'attributes': {'str': {'value': 3}, 'spd': {'value': 2}},
                }}})
            out.instance_id = name.lower()
            out.conditions = {'prone': True, 'frightened': 2}
            out.skills = {
                'hwp': {'rank': 2, 'attribute': 'str', 'mod': 8, 'unlocked': True},
                'abr': {'rank': 0, 'attribute': 'spd', 'mod': 9, 'unlocked': False},
            }
            out.strikes = [{'name': 'Glass Blade', 'bonus': 8, 'damage': '2d6 keen'}]
            out.actions = [{'name': 'Press the Attack', 'description': 'Advance.', 'actions': 1}]
            return out

        A.ACTIVE_ENCOUNTER[:] = [actor('Vin', 'character'), actor('Hazekiller', 'adversary')]
        client = A.app.test_client()
        pc = client.get('/api/combatant_stats/vin')
        npc = client.get('/api/combatant_stats/hazekiller')
        assert pc.status_code == 200, pc.data
        assert npc.status_code == 200, npc.data
        pc_data, npc_data = pc.get_json(), npc.get_json()
        for data in (pc_data, npc_data):
            assert data['system'] == 'cosmere'
            assert data['cosmere']['tier'] == 2
            assert data['attacks'][0] == {'name': 'Glass Blade', 'hit': '+8', 'damage': '2d6 keen'}
            assert data['skills'] == [{'name': 'Heavy Weaponry', 'total': 8}]
            assert data['conditions'] == {'prone': True}
            assert 'class_name' not in data and 'abilities' not in data
        assert npc_data['actions'][0]['name'] == 'Press the Attack'
        print('COSMERE_FULL_STATS_OK')
    ''')
    assert 'COSMERE_FULL_STATS_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)
