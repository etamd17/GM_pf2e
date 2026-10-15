"""Revision B: engine budget guidance and the shared private draft bar."""
from copy import deepcopy
import json
from pathlib import Path
import re
import subprocess

from flask import render_template, template_rendered
import pytest

import app as A
from tests.test_cosmere_builder import _VALID, _cleanup

ROOT = Path(__file__).resolve().parents[1]
BUILDER = (ROOT / 'templates/cosmere_builder.html').read_text(encoding='utf-8')
CSS = (ROOT / 'static/css/system.css').read_text(encoding='utf-8')
IDS = ('draft-status', 'draft-save', 'draft-copy', 'draft-reload', 'draft-discard')


def node(script):
    result = subprocess.run(['node', '-e', script], capture_output=True,
                            text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def guide_code(page=BUILDER):
    assert 'const GUIDE_BUDGETS =' in page, 'engine budget guide is missing'
    return page[page.index('const GUIDE_BUDGETS ='):page.index('function goStep(i)')]


@pytest.mark.parametrize('edit', [False, True], ids=['new', 'edit'])
def test_builder_renders_revision_b(edit):
    client = A.app.test_client()
    pid = None
    if edit:
        build = deepcopy(_VALID)
        build['level'] = 5
        pid = client.post('/cosmere/builder', json={'build': build}).get_json()['id']
    try:
        page = client.get('/cosmere/builder' + ('?pc=' + pid if pid else '')).get_data(as_text=True)
        title = 'Edit or level up' if edit else 'New Cosmere Character'
        assert re.search(r'<h2[^>]*>' + title + r'</h2>', page)
        assert ('Edit or level up' in page) == edit
        assert '<div id="still-to-choose"' in page
        assert 'id="level-up-badge"' in page
        assert re.search(r'id="level-up-badge"[^>]*hidden', page)
        glossary = page[page.index('<details class="cb-glossary">'):page.index('</details>')]
        assert '<summary>How this builder works, and key terms</summary>' in glossary
        assert 'A guided, step-by-step build.' in glossary
        assert page.count('A guided, step-by-step build.') == 1
    finally:
        if pid:
            _cleanup(pid)


@pytest.mark.parametrize('template', ['cosmere_builder.html', 'player_builder.html',
                                    'character_import_preview.html', 'character_publication_recovery.html'])
def test_shared_draft_bar_on_every_include_page(template):
    workflow = dict(enabled=True, recovering=False, targetName='', cancelUrl='/me',
                    initialDraft={'inputs': {'submission': {'build': {'name': 'Guide test'}}}})
    with A.app.test_request_context('/player/builder'):
        if template == 'cosmere_builder.html':
            contexts = []
            def capture(sender, template, context, **extra):
                if template.name == 'cosmere_builder.html':
                    contexts.append(context)
            with template_rendered.connected_to(capture, A.app):
                A.app.test_client().get('/cosmere/builder')
            context = dict(contexts[0], workflow=workflow)
            page = render_template(template, **context)
            include = page
        else:
            page = render_template(template, workflow=workflow, classes=[], ancestries=[],
                                   backgrounds=[], spells=[], feats=[], builder_data={},
                                   subclass_descriptions={}, weapons=[], armor=[])
            include = page
        assert 'css/system.css' in page
        assert 'class="character-draft-controls draft-bar"' in include
        for id_ in IDS:
            assert include.count('id="' + id_ + '"') == 1
        for id_ in ('draft-copy', 'draft-reload'):
            assert re.search(r'id="' + id_ + r'" hidden', include)
        assert 'js/character_drafts.js' in include
        assert 'Resume drafts' in include


def test_draft_bar_uses_shared_css_and_honors_hidden():
    partial = (ROOT / 'templates/_character_draft_controls.html').read_text(encoding='utf-8')
    assert 'style=' not in partial
    assert re.search(r'\.draft-bar\s*\{[^}]*display:\s*flex', CSS)
    assert re.search(r'\.draft-bar button\[hidden\]\s*\{\s*display:\s*none', CSS)
    assert re.search(r'\.level-up-badge\[hidden\]\s*\{\s*display:\s*none', BUILDER)


@pytest.mark.parametrize('budgets, labels, left, over', [
    ({'attr': [12, 13], 'skills': [13, 15], 'expertises': [2, 3], 'talents': [6, 8]},
     ['1 attribute point', '2 skill ranks', '1 expertise', '2 talents'], [1, 3, 2], [False]*3),
    ({'attr': [0, 2], 'skills': [0, 1], 'expertises': [0, 2], 'talents': [0, 1]},
     ['2 attribute points', '1 skill rank', '2 expertises', '1 talent'], [2, 3, 1], [False]*3),
    ({'attr': [13, 13], 'skills': [15, 15], 'expertises': [3, 3], 'talents': [8, 8]},
     [], [0, 0, 0], [False]*3),
    ({'attr': [14, 13], 'skills': [16, 15], 'expertises': [2, 3], 'talents': [9, 8]},
     ['1 expertise'], [0, 1, 0], [True]*3),
])
def test_budget_mapping_uses_engine_remaining_and_step_destinations(budgets, labels, left, over):
    guide = node(guide_code() + '\nprocess.stdout.write(JSON.stringify(budgetGuide(' + json.dumps(budgets) + ')));')
    assert [r['label'] for r in guide['rows']] == labels
    assert [guide['steps'][str(i)]['left'] for i in (1, 2, 3)] == left
    assert [guide['steps'][str(i)]['over'] for i in (1, 2, 3)] == over
    destinations = {'attr': 1, 'skills': 2, 'expertises': 2, 'talents': 3}
    assert all(r['step'] == destinations[r['key']] for r in guide['rows'])


def test_actual_preview_paints_guide_and_rail_from_engine_budgets():
    page = A.app.test_client().get('/cosmere/builder').get_data(as_text=True)
    paint = page[page.index('function paintPreview(p){'):page.index('// ── rules gate:')]
    guide = guide_code(page)
    status = page[page.index('function setRailStatus('):page.index('const GUIDE_BUDGETS =')]
    result = node('''
const elements={}, tabs={};
for(const i of [1,2,3]) tabs[i]={chk:{},left:{},querySelector(s){return s==='.wiz-chk'?this.chk:this.left;},setAttribute(k,v){this[k]=v;},getAttribute(k){return this[k];}};
const document={getElementById(id){return elements[id] ||= {style:{}};},querySelector(s){return tabs[+s.match(/data-i="([0-9]+)"/)[1]];}};
const ORDERS={}, STEPS=['Identity','Attributes','Skills','Heroic Path'], INITIAL_LEVEL=5, lvl=()=>6, v=()=>'', updateSaveGate=()=>{};
''' + status + guide + paint + '''
const p={level:6,tier:2,defenses:{},budgets:{attr:[12,13],skills:[15,15],expertises:[2,3],talents:[8,8]}};
paintPreview(p);
const first=elements['still-to-choose'].innerHTML, partial=JSON.parse(JSON.stringify(tabs));
p.budgets={attr:[13,13],skills:[16,15],expertises:[3,3],talents:[8,8]};paintPreview(p);
process.stdout.write(JSON.stringify({first,partial,tabs,empty:elements['still-to-choose'].innerHTML}));
''')
    assert '1 attribute point' in result['first'] and '1 expertise' in result['first']
    assert 'goStep(1)' in result['first'] and 'goStep(2)' in result['first']
    assert result['partial']['1']['left']['textContent'] == '1 left'
    assert result['partial']['2']['chk']['className'] == 'wiz-chk'
    assert result['partial']['3']['chk']['className'] == 'wiz-chk done'
    assert result['tabs']['2']['chk']['className'] == 'wiz-chk over'
    assert 'Nothing left to choose.' in result['empty']


def test_level_badge_compares_with_page_load_level():
    assert 'const INITIAL_LEVEL = {{ build.level|tojson }};' in BUILDER
    code = guide_code()
    result = node('''
const badge={};let current=5;const INITIAL_LEVEL=5,lvl=()=>current;
const document={getElementById:()=>badge};
''' + code + '''
const states=[];for(current of [5,6,4,5]){paintLevelBadge();states.push({...badge});}
process.stdout.write(JSON.stringify(states));
''')
    assert [s['hidden'] for s in result] == [True, False, True, True]
    assert result[1]['textContent'] == 'Leveling up: 5 -> 6'
    assert re.search(r'function onLevel\(\)\{[^}]*paintLevelBadge\(\)', BUILDER)


def test_controller_and_layout_preserve_preview_and_collect_contract():
    assert "body:JSON.stringify({id:PID, build:collect()})" in BUILDER
    assert '.then(r=>r.json()).then(p=>' in BUILDER
    assert 'LAST_PREVIEW=p; paintPreview(p);' in BUILDER
    assert 'window.fetch =' not in BUILDER and 'window.fetch=' not in BUILDER
    assert 'function collect(){' in BUILDER
    assert 'body:JSON.stringify({id:PID, build:collect(), force:GM_OVERRIDE})' in BUILDER
    recompute = BUILDER[BUILDER.index('function recompute(){'):BUILDER.index('const STEPS =')]
    assert not re.search(r'setRailStatus\([123],', recompute), 'local estimates must not mark budget steps complete'
    assert re.search(r'\.wiz-rail\s*\{[^}]*position:sticky', BUILDER)
    assert 'grid-template-columns:minmax(0,1fr) 290px' in BUILDER
    assert '@media (max-width:760px)' in BUILDER
    assert 'overflow-x:auto' in BUILDER and 'flex-wrap:nowrap' in BUILDER


def test_builder_sticky_elements_have_a_real_scroll_container():
    assert re.search(r'body:has\(\.bld\.wiz\)\s*\{[^}]*height:100vh;[^}]*overflow:hidden', BUILDER)
    assert re.search(r'body:has\(\.bld\.wiz\) > \.overflow-y-auto\s*\{[^}]*min-height:0', BUILDER)
    assert re.search(r'\.topbar\s*\{[^}]*position:sticky;[^}]*top:0', BUILDER)
    assert '--builder-topbar-height' in BUILDER and 'ResizeObserver' in BUILDER
    mobile = BUILDER[BUILDER.index('@media (max-width:760px)'):BUILDER.index('.step {')]
    assert '.topbar { position:static; }' in mobile and 'top:0;' in mobile


def test_preview_responses_cannot_replace_a_newer_engine_guide():
    request = BUILDER[BUILDER.index('function doPreview(){'):BUILDER.index('let LAST_PREVIEW=null;')]
    result = node('''
const requests=[],painted=[],PID='saved-id';let LAST_PREVIEW=null;
const collect=()=>({level:6}),paintPreview=p=>painted.push(p.level);
const fetch=(url,options)=>new Promise(resolve=>requests.push({resolve,url,body:JSON.parse(options.body)}));
''' + request + '''
doPreview();doPreview();
requests[1].resolve({json:()=>Promise.resolve({level:6})});
setImmediate(()=>{requests[0].resolve({json:()=>Promise.resolve({level:5})});
setImmediate(()=>process.stdout.write(JSON.stringify({painted,last:LAST_PREVIEW.level,bodies:requests.map(r=>r.body)})));});
''')
    assert result['painted'] == [6], result
    assert result['last'] == 6
    assert all(body == {'id': 'saved-id', 'build': {'level': 6}} for body in result['bodies'])
