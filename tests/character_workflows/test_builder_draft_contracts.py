"""Builder adapters execute under Node; GET bootstrap crosses real authorization."""
import pytest
from tests.character_workflows.test_draft_controller import ROOT, node
from tests.character_workflows.test_http import SETUP
from tests.persistence.test_runtime_http import run_sql


def adapter(name):
    source = (ROOT / 'templates' / name).read_text(encoding='utf-8')
    assert '// DRAFT ADAPTER START' in source
    return source.split('// DRAFT ADAPTER START', 1)[1].split('// DRAFT ADAPTER END', 1)[0]


def test_pf2e_non_dom_choices_round_trip():
    source = adapter('player_builder.html')
    node(r'''
const fields = {'inp-name':{value:'Draft hero'},'inp-deity':{value:'Iomedae'},
 'inp-level':{value:'3'},'sel-anc-method':{value:'alternate'}};
document.getElementById=id=>fields[id];
let state={name:'Draft hero',cls:null,ancBoosts:['str','dex'],guided:{kin_elements:'Fire'},
 clsFeats:{one:{name:'Choice'}},spells:[{name:'Light',level:0}]};
const pfDraftDefaults=clone(state);
let pfDraftStep='skills',draftRestoring=false,changes=0;
let draftController={changed:()=>{if(!draftRestoring)changes++}};
function collectPf2eSubmission(){return {name:fields['inp-name'].value};}
function repaintPf2eDraft(){assert.equal(fields['sel-anc-method'].value,'alternate');state.ancBoosts=[];draftController.changed();}
function navTo(step){pfDraftStep=step;draftController.changed();}
function runValidation(){draftController.changed();}
''' + source + r'''
const saved=collectPf2eDraft(); state.guided={}; state.spells=[]; fields['inp-deity'].value='';
await restorePf2eDraft(saved);
assert.deepEqual(collectPf2eDraft(),saved); assert.equal(changes,0);
assert.equal(saved.ui.ancestryMethod,'alternate'); assert.equal(saved.ui.step,'skills');
''')


def test_cosmere_restore_suppresses_initialization_saves():
    source = adapter('cosmere_builder.html')
    node(r'''
const fields=[{id:'f-name',value:''},{id:'f-order',value:'windrunner'},
 {id:'f-variant',value:'saved-variant'},{id:'f-expertises',value:'unfinished\n'}];
const skills=[{dataset:{code:'ath'},value:'2'},{dataset:{code:'sur'},value:'0'}];
document.querySelectorAll=s=>s==='.skill-rank'?skills:fields;
document.getElementById=id=>fields.find(f=>f.id===id);
let TALENTS=['chosen'],INV=[{name:'sword'}],FABRIALS=[{name:'fab'}],INFECTED=['art'],IDEAL_WORDS=['word'];
let STEP=4,draftRestoring=false,changes=0;
function collect(){return {name:fields[0].value||'New Hero',talents:TALENTS,inventory:INV,
 fabrials:FABRIALS,infected_arts:INFECTED,ideal_words:IDEAL_WORDS};}
function recompute(){if(!draftRestoring)changes++;}
function onPath(){TALENTS=['default'];skills[0].value='1';recompute();}
function onAncestry(){TALENTS.push('ancestry');recompute();}
function onOrder(){fields[2].value='';IDEAL_WORDS=[];recompute();}
function onVariant(){INV=[];recompute();}
function paintCultureInfo(){}
function renderTalents(){} function renderInv(){} function renderFabrials(){}
function renderInfected(){} function renderIdeals(){}
function goStep(s){STEP=s;recompute();}
''' + source + r'''
const saved=collectCosmereDraft(); TALENTS=[];INV=[];STEP=0;
await restoreCosmereDraft(saved);
assert.deepEqual(collectCosmereDraft(),saved); assert.equal(changes,0);
assert.equal(STEP,4); assert.equal(skills[0].value,'2'); assert.equal(fields[0].value,'');
''')


@pytest.mark.parametrize('backend', ['json', 'sql'])
@pytest.mark.parametrize('system', ['pf2e', 'cosmere'])
def test_builder_resume_get_is_private_and_creates_nothing(tmp_path, backend, system):
    run_sql(tmp_path, 'SYSTEM = ' + repr(system) + '\n' + SETUP + '''
url = '/player/builder' if SYSTEM == 'pf2e' else '/cosmere/builder'
fresh = client.get(url)
assert fresh.status_code == 200, fresh.data[:400]
assert b'id="draft-save"' in fresh.data
assert b'GM override: ignore prerequisites' not in fresh.data
assert client.get(BASE).json['drafts'] == []
draft = create()
resume = url + '?draft_id=' + draft['id']
page = client.get(resume)
assert page.status_code == 200 and draft['id'].encode() in page.data, page.data[:400]
assert other_client.get(resume).status_code == 404
assert client.get(resume + '&pc=missing').status_code == 409
assert client.get(url + '?target_id=missing').status_code == 404
assert len(client.get(BASE).json['drafts']) == 1
roster = client.get('/player?roster=1' if SYSTEM == 'pf2e' else '/cosmere/pcs')
assert roster.status_code == 200
assert resume.encode() in roster.data and b'Resume drafts' in roster.data
assert b'Claim this character' not in roster.data
''', backend=backend)


@pytest.mark.parametrize('backend', ['json', 'sql'])
def test_import_preview_target_and_roster_links_follow_capabilities(tmp_path, backend):
    run_sql(tmp_path, "SYSTEM = 'pf2e'\n" + SETUP + '''
draft = create()
published = client.post(BASE + '/' + draft['id'] + '/publish',
    json={'expected_revision': 0, 'request_key': 'publish'}, headers=HEADERS).json
chid = published['character_id']
owner_roster = client.get('/player?roster=1')
assert owner_roster.status_code == 200, owner_roster.data[:400]
assert ('value="' + chid + '"').encode() in owner_roster.data
assert b'Owned' in owner_roster.data
assert b'title="Bookmark this character"' not in owner_roster.data
outsider_roster = other_client.get('/player?roster=1')
assert ('href="/characters/' + chid + '"').encode() not in outsider_roster.data
assert ('value="' + chid + '"').encode() not in outsider_roster.data
prepared = client.post('/api/character-workflows/imports',
    json={'source': {'build': {'name': 'Workflow Hero', 'class': 'Fighter', 'ancestry': 'Human', 'level': 2}},
          'target_id': chid, 'request_key': 'update-preview'}, headers=HEADERS)
assert prepared.status_code == 201, prepared.data
preview = client.get('/player/builder?draft_id=' + prepared.json['draft']['id'])
assert preview.status_code == 200, preview.data[:400]
assert b'Confirm character import' in preview.data and b'Update Workflow Hero' in preview.data
assert b'Confirm and publish' in preview.data
assert len(client.get(BASE).json['drafts']) == 1
assert b'Resume drafts' in client.get('/me').data
''', backend=backend)


@pytest.mark.parametrize('backend', ['json', 'sql'])
@pytest.mark.parametrize('system', ['pf2e', 'cosmere'])
@pytest.mark.parametrize('update', [False, True])
def test_pending_publication_resume_get_is_retry_only(tmp_path, backend, system, update):
    run_sql(tmp_path, 'SYSTEM = ' + repr(system) + '\nUPDATE = ' + repr(update) + '\n' + SETUP + '''
from core.character_workflows import publication, recovery
draft = create()
if UPDATE:
    first = client.post(BASE + '/' + draft['id'] + '/publish',
        json={'expected_revision': 0, 'request_key': 'first'}, headers=HEADERS)
    assert first.status_code == 200, first.data
    if SYSTEM == 'pf2e':
        inputs.update(kind='pf2e_import', submission={'build': {'name':'Workflow Hero',
            'class':'Fighter', 'ancestry':'Human', 'level':2}})
    else:
        inputs['submission']['build']['level'] = 2
    response = client.post(BASE, json={'inputs':inputs,'request_key':'update',
        'target_id': first.json['character_id']}, headers=HEADERS)
    assert response.status_code == 201, response.data
    draft = response.json['draft']
original = publication.complete_publication
def lose_completion(*args, **kwargs):
    raise RuntimeError('injected completion failure')
publication.complete_publication = lose_completion
response = client.post(BASE + '/' + draft['id'] + '/publish',
    json={'expected_revision':0,'request_key':'uncertain'}, headers=HEADERS)
assert response.status_code == 503, response.data
publication.complete_publication = original
recovery.invalidate_pending_index(cid)  # fresh pending-index discovery, as on restart
saved = client.get(BASE + '/' + draft['id']).json['draft']
assert saved['state'] == 'publishing'
url = ('/player/builder' if SYSTEM == 'pf2e' else '/cosmere/builder') + '?draft_id=' + draft['id']
resumed = client.get(url)
assert resumed.status_code == 200, resumed.data[:500]
assert b'Publication needs recovery' in resumed.data
assert b'id="draft-save"' in resumed.data
assert b'id="inp-name"' not in resumed.data and b'id="f-name"' not in resumed.data
assert other_client.get(url).status_code == 404
assert client.get(BASE + '/' + draft['id']).json['draft'] == saved
retried = client.post(BASE + '/' + draft['id'] + '/publish',
    json={'expected_revision':saved['revision'],'request_key':'after-restart'}, headers=HEADERS)
assert retried.status_code == 200, retried.data
assert client.get(retried.json['url']).status_code == 200
''', backend=backend)


@pytest.mark.parametrize('backend', ['json', 'sql'])
@pytest.mark.parametrize('system', ['pf2e', 'cosmere'])
def test_committed_draft_resume_redirects_only_authorized_result(tmp_path, backend, system):
    run_sql(tmp_path, 'SYSTEM = ' + repr(system) + '\n' + SETUP + '''
draft = create()
response = client.post(BASE + '/' + draft['id'] + '/publish',
    json={'expected_revision':0,'request_key':'publish'}, headers=HEADERS)
assert response.status_code == 200, response.data
url = ('/player/builder' if SYSTEM == 'pf2e' else '/cosmere/builder') + '?draft_id=' + draft['id']
resumed = client.get(url)
assert resumed.status_code == 302 and resumed.location == response.json['url'], resumed.data[:500]
assert other_client.get(url).status_code == 404
from core.persistence import CharacterAssignment, runtime
if runtime.sql_enabled():
    from sqlalchemy import update
    with runtime.database().transaction() as db:
        db.execute(update(CharacterAssignment).where(
            CharacterAssignment.character_id == response.json['character_id'],
            CharacterAssignment.role == 'owner').values(user_id=other['id']))
else:
    directory = storage.party_dir(cid) if SYSTEM == 'pf2e' else storage.cosmere_pc_dir(cid)
    path = Path(directory) / (response.json['character_id'] + '.json')
    doc = json.loads(path.read_text())
    doc['owner_user_id'] = other['id']
    storage.atomic_write_json(str(path), doc)
assert client.get(url).status_code == 404
''', backend=backend)
