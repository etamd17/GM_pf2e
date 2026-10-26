"""Independent currencies, exact reversals, read-only projection and real saves."""
import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from jinja2 import Environment
from systems.cosmere import scadrial_money as money

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('raw', [None, [], 'bad', {}, {'scadrial': None}, {'scadrial': {'clip': 'bad', 'boxing': -9}}])
def test_missing_partial_bad_wallets_are_detached(raw):
    before = copy.deepcopy(raw)
    result = money.normalize_wallet(raw)
    assert result['scadrial'] == {'version': 1, 'boxing': 0, 'clip': 0}
    assert raw == before


def test_exact_money_and_independent_spheres():
    raw = {'spheres': {'chip': 123}, 'scadrial': {'boxing': '2', 'clip': 103}, 'items': []}
    result = money.normalize_wallet(raw)
    assert result['spheres'] == raw['spheres']
    assert money.wallet_total(raw) == 303
    assert money.format_money(303) == '3 boxings, 3 clips'
    result['spheres']['chip'] = 0
    assert raw['spheres']['chip'] == 123
    assert money.wallet_total({'scadrial': {'boxing': money.MAX_COUNT, 'clip': 99}}) == 100 * money.MAX_COUNT + 99


@pytest.mark.parametrize('value', [-1, True, 1.5, '2', None, money.MAX_COUNT + 1])
def test_bad_write_is_refused(value):
    with pytest.raises(ValueError):
        money.validate_money({'boxing': value})


@pytest.fixture
def routes(tmp_path, monkeypatch):
    import app as A
    pid = 'd' * 32
    docs = {pid: {'id': pid, 'name': 'Vin', 'wallet': {
        'spheres': {'chip': 3, 'mark': 2, 'broam': 1},
        'items': [{'name': 'Rope', 'qty': 2}],
    }}}
    saves, events = [], []
    world = ['mistborn']
    monkeypatch.setattr(A, '_active_system', lambda: 'cosmere')
    monkeypatch.setattr(A, '_cosmere_world', lambda: world[0])
    monkeypatch.setattr(A, '_account_mode', lambda: False)
    monkeypatch.setattr(A, '_is_gm', lambda: True)
    monkeypatch.setattr(A, '_cosmere_can_act_on', lambda _: True)
    monkeypatch.setattr(A, '_list_cosmere_pcs', lambda: copy.deepcopy(list(docs.values())))
    monkeypatch.setattr(A, '_load_cosmere_pc', lambda pid: copy.deepcopy(docs.get(pid)))
    def save(doc, **kwargs):
        docs[doc['id']] = copy.deepcopy(doc)
        saves.append(copy.deepcopy(doc))
    monkeypatch.setattr(A, '_save_cosmere_pc', save)
    monkeypatch.setattr(A, 'LOOT_LEDGER_FILE', str(tmp_path / 'loot.json'))
    monkeypatch.setattr(A, 'sse_broadcast', lambda name, payload, **kw: events.append((name, copy.deepcopy(payload))))
    return A, A.app.test_client(), pid, docs, saves, events, world


def award(client, **fields):
    return client.post('/api/cosmere/loot/add', json={'recipient': 'Vin', **fields})


def test_award_totals_and_exact_reversal_after_world_switch(routes):
    A, client, pid, docs, saves, events, world = routes
    original = copy.deepcopy(docs[pid]['wallet'])
    assert award(client, scadrial={'boxing': 2, 'clip': 7}, items=[{'name': 'Rope', 'qty': 3}]).status_code == 200
    wallet = docs[pid]['wallet']
    assert wallet['spheres'] == original['spheres']  # even the legacy shape stays intact
    assert wallet['scadrial'] == {'version': 1, 'boxing': 2, 'clip': 7}
    data = client.get('/api/cosmere/loot').get_json()
    entry = data['entries'][0]
    assert entry['currency'] == 'scadrial' and entry['value_clips'] == 207
    assert data['total_money'] == data['party_wallet_total'] == '2 boxings, 7 clips'
    assert data['party'][0]['wallet_total_clips'] == 207
    assert events[-1][1]['wallet_total_money'] == '2 boxings, 7 clips'
    world[0] = 'stormlight'
    assert client.post('/api/cosmere/loot/delete', json={'id': entry['id']}).status_code == 200
    assert docs[pid]['wallet']['spheres'] == original['spheres']
    assert money.wallet_total(docs[pid]['wallet']) == 0
    assert docs[pid]['wallet']['items'] == original['items']


def test_sphere_award_reversal_never_debits_boxings(routes):
    _, client, pid, docs, _, _, world = routes
    docs[pid]['wallet']['scadrial'] = {'version': 1, 'boxing': 8, 'clip': 9}
    world[0] = 'stormlight'
    assert award(client, spheres={'chip': 2}, gem='ruby').status_code == 200
    entry = client.get('/api/cosmere/loot').get_json()['entries'][0]
    world[0] = 'mistborn'
    assert client.post('/api/cosmere/loot/delete', json={'id': entry['id']}).status_code == 200
    assert docs[pid]['wallet']['scadrial'] == {'version': 1, 'boxing': 8, 'clip': 9}
    assert 'ruby' not in docs[pid]['wallet']['spheres']


def test_spent_denomination_cannot_be_reversed_from_equal_value_other_money(routes):
    _, client, pid, docs, saves, _, _ = routes
    assert award(client, scadrial={'boxing': 1}).status_code == 200
    entry = client.get('/api/cosmere/loot').get_json()['entries'][0]
    docs[pid]['wallet']['scadrial'].update(boxing=0, clip=100)
    before = copy.deepcopy(docs[pid])
    assert client.post('/api/cosmere/loot/delete', json={'id': entry['id']}).status_code == 409
    assert docs[pid] == before
    assert len(client.get('/api/cosmere/loot').get_json()['entries']) == 1
    assert len(saves) == 1


def test_wrong_world_and_fractional_awards_do_not_write(routes):
    _, client, _, _, saves, _, world = routes
    assert award(client, spheres={'chip': 1}).status_code == 400
    assert award(client, scadrial={'clip': 0.5}).status_code == 400
    world[0] = 'stormlight'
    assert award(client, scadrial={'boxing': 1}).status_code == 400
    assert not saves


def test_wallet_editor_roundtrip_stale_and_permissions(routes, monkeypatch):
    A, client, pid, docs, saves, events, world = routes
    url = f'/cosmere/pc/{pid}/wallet'
    original = copy.deepcopy(docs[pid]['wallet'])
    data = {'scadrial': {'boxing': 7, 'clip': 8}, 'expected': {'boxing': 0, 'clip': 0}}
    assert client.post(url, json=['invalid']).status_code == 400
    response = client.post(url, json=data)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['wallet_total_clips'] == 708
    assert docs[pid]['wallet']['spheres'] == original['spheres']
    assert events[-1][0] == 'cosmere_player_state'
    assert events[-1][1]['wallet']['scadrial']['boxing'] == 7
    assert client.post(url, json=data).status_code == 409  # old editor cannot overwrite an intervening award
    monkeypatch.setattr(A, '_is_gm', lambda: False)
    # Ownership/edit capability alone must not allow a player to mint money.
    assert A._cosmere_can_act_on(docs[pid])
    assert client.post(url, json=data).status_code == 403
    assert len(saves) == 1
    world[0] = 'stormlight'
    assert client.post(url, json=data).status_code == 400


def test_mistborn_sheet_open_never_rewrites_and_save_retains_spheres(tmp_path, monkeypatch):
    import app as A
    pid = 'e' * 32
    path = tmp_path / (pid + '.json')
    document = {'id': pid, 'system': 'cosmere', 'name': 'Legacy', 'owner_user_id': None,
                'build': {'name': 'Legacy', 'level': 1, 'path': 'warrior',
                          'attributes': dict.fromkeys(('str', 'spd', 'int', 'wil', 'awa', 'pre'), 2)},
                'wallet': {'spheres': {'chip': 7}, 'items': []}}
    path.write_text(json.dumps(document, indent=2), encoding='utf-8')
    monkeypatch.setattr(A, 'COSMERE_PC_DIR', str(tmp_path))
    monkeypatch.setattr(A, '_active_system', lambda: 'cosmere')
    monkeypatch.setattr(A, '_cosmere_world', lambda: 'mistborn')
    monkeypatch.setattr(A, '_account_mode', lambda: False)
    monkeypatch.setattr(A, '_is_gm', lambda: True)
    monkeypatch.setattr(A, '_request_campaign_config', lambda: {'cosmere_world': 'mistborn'})
    monkeypatch.setattr(A, 'CAMPAIGN_DEFAULT', {**A.CAMPAIGN_DEFAULT, 'cosmere_world': 'mistborn'})
    monkeypatch.setattr(A, 'sse_broadcast', lambda *a, **k: None)
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns
    client = A.app.test_client()
    page = client.get(f'/cosmere/pc/{pid}')
    assert page.status_code == 200
    assert b'Save money' in page.data and b'Roshar spheres (read-only' in page.data
    assert path.read_bytes() == before and path.stat().st_mtime_ns == mtime
    response = client.post(f'/cosmere/pc/{pid}/wallet', json={'scadrial': {'boxing': 3}, 'expected': {}})
    assert response.status_code == 200, response.get_json()
    persisted = json.loads(path.read_text(encoding='utf-8'))
    assert persisted['wallet']['spheres'] == {'chip': 7}
    assert persisted['wallet']['scadrial']['boxing'] == 3


def test_stormlight_wallet_output_and_live_handler_match_pre_mb04_bytes():
    # Captured from the pre-MB04 template, with a fixed representative wallet.
    source = (ROOT / 'templates/cosmere_sheet.html').read_text(encoding='utf-8')
    index = source.index('  <div class="cs-section-h cs-foldable">Spheres &amp; Goods')
    start = source.rfind('  <section>', 0, index)
    end = source.index('  <section>', start + 12)
    rendered = Environment().from_string(source[start:end]).render(
        pc=True, cosmere_world='stormlight',
        wallet={'spheres': {'emerald': {'chip': 2, 'mark': 1, 'broam': 3}}, 'items': [{'name': 'Rope', 'qty': 2}]},
        wallet_total_marks='661')
    assert hashlib.sha256(rendered.encode()).hexdigest() == 'f56f1810c6f4673baf33ef005d1934f5d506f42f84b546f2015a4da0aa645973'
    start = source.index("{% if cosmere_world != 'mistborn' %}      // GM awarded")
    end = source.index('\n    } catch(e) {}', start)
    rendered = Environment().from_string(source[start:end]).render(cosmere_world='stormlight')
    assert hashlib.sha256(rendered.encode()).hexdigest() == '87628dc2626f087727a802e3d9c6fe572b00e62069bed27a8991c5703f193fba'
    assert hashlib.sha256((ROOT / 'templates/cosmere_loot.html').read_text(encoding='utf-8').encode()).hexdigest() == '906105b884ec84b1bad817a1c17c9c421cf4c905e62f8a61757ac95b252abc73'


@pytest.mark.parametrize('reader', ['gm', 'player'])
def test_real_wallet_script_repaints_and_preserves_stale_edit_base(reader):
    node = shutil.which('node')
    if not node:
        pytest.skip('node is unavailable')
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
  const readonly = process.argv[2] === 'player';
  const handlers={}, events={}, calls=[];
  const element=()=>({textContent:'',children:[],hidden:false,replaceChildren(){this.children=[];},appendChild(n){this.children.push(n);}});
  const boxing=element(),clip=element(),total=element(),goods=element(),status=element(),legacy=element(),counts=element(),button={disabled:false};
  const form={elements:{boxing:{value:'0'},clip:{value:'0'}},reportValidity:()=>true,addEventListener:(n,h)=>handlers[n]=h,querySelector:()=>button};
  const root={dataset:{pid:'pc'},querySelector:s=>({'[data-money-form]':readonly ? null : form,'[data-money-status]':readonly ? null : status,
    '[data-money-count="boxing"]':boxing,'[data-money-count="clip"]':clip,
    '[data-money-total]':total,'[data-money-goods]':goods,'[data-money-legacy]':legacy,'[data-money-legacy-counts]':counts,
    '[data-money-initial]':{textContent:'{"version":1,"boxing":0,"clip":0}'}}[s])};
  const frame=(n)=>({pid:'pc',wallet:{scadrial:{version:1,boxing:n,clip:0},spheres:{chip:7},items:[{name:"<Rope>",qty:2}]},wallet_total_money:n+' boxings, 0 clips'});
  vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{
    document:{getElementById:()=>root,createElement:()=>element()},window:{appSSE:(n,h)=>events[n]=h},
    fetch:async(url,options)=>{calls.push(JSON.parse(options.body));return {ok:true,json:async()=>({ok:true,...frame(9)})};}
  });
  events.cosmere_loot({data:JSON.stringify(frame(2))});
  assert.equal(boxing.textContent,2);assert.equal(clip.textContent,0);
  assert.equal(total.textContent,'2 boxings, 0 clips');
  if (readonly) {
    assert.equal(Object.keys(events).length,2);assert.equal(Object.keys(handlers).length,0);
    events.cosmere_player_state({data:JSON.stringify(frame(3))});
    assert.equal(boxing.textContent,3);assert.equal(total.textContent,'3 boxings, 0 clips');
    assert.equal(calls.length,0);return;
  }
  assert.equal(form.elements.boxing.value,2);
  assert.equal(goods.children[0].textContent,'<Rope> ×2');assert.equal(legacy.hidden,false);
  form.elements.boxing.value='9';handlers.input();
  events.cosmere_player_state({data:JSON.stringify(frame(3))});
  assert.equal(form.elements.boxing.value,'9');assert.match(status.textContent,/changed elsewhere/);
  await handlers.submit({preventDefault(){}});
  assert.equal(calls.length,1);assert.equal(calls[0].expected.boxing,2);assert.equal(calls[0].scadrial.boxing,9);
  assert.equal(status.textContent,'Saved');assert.equal(button.disabled,false);
  const empty=frame(0);empty.wallet.spheres={};events.cosmere_loot({data:JSON.stringify(empty)});
  assert.equal(legacy.hidden,true);
  const foreign=frame(44);foreign.pid='other';events.cosmere_loot({data:JSON.stringify(foreign)});
  assert.equal(form.elements.boxing.value,0);
})().catch(error=>{console.error(error);process.exit(1);});
'''
    result = subprocess.run([node, '-e', script, str(ROOT / 'static/js/cosmere_scadrial_wallet.js'), reader],
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('gm', [True, False])
def test_money_wallet_editor_is_absent_for_players(gm):
    import app as A
    from flask import render_template
    with A.app.test_request_context():
        html = render_template('_cosmere_scadrial_wallet.html', actor_id='a' * 32,
                               wallet={'scadrial': {'version': 1, 'boxing': 1, 'clip': 1}, 'items': []},
                               wallet_total_money='1 boxing, 1 clip', legacy_spheres={},
                               wallet_editable=gm, interactive=True)
    assert ('data-money-form' in html) is gm
    assert ('Save money' in html) is gm
    assert ('type="number"' in html) is gm
    assert 'data-money-count="boxing">1</b>' in html
    assert 'data-money-count="clip">1</b>' in html
    assert 'Boxings &amp; Goods' in html and '(awarded by the GM)' in html
    assert 'Worth' in html and '1 boxing, 1 clip' in html


@pytest.mark.parametrize('clips,expected', [
    (0, '0 boxings, 0 clips'), (1, '0 boxings, 1 clip'),
    (100, '1 boxing, 0 clips'), (101, '1 boxing, 1 clip'), (201, '2 boxings, 1 clip'),
])
def test_money_formats_singular_and_plural_units(clips, expected):
    assert money.format_money(clips) == expected
