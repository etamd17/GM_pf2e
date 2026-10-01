"""Execute the real browser controller in Node with deterministic network gates."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def node(source):
    harness = r'''
const assert = require('node:assert/strict');
global.window = global;
global.document = {querySelector: () => ({content:'csrf-test'})};
const events = {};
global.addEventListener = (name, fn) => { events[name] = fn; };
global.removeEventListener = (name) => { delete events[name]; };
const deferred = () => {let resolve; const promise = new Promise(r=>resolve=r); return {resolve,promise};};
const tick = () => new Promise(r=>setImmediate(r));
const clone = x => JSON.parse(JSON.stringify(x));
const reply = (data, status=200) => ({ok:status<400,status,json:async()=>data});
const input = n => ({kind:'pf2e_builder',payload_version:1,form:{name:n},ui:{step:2},submission:{name:n}});
let local=input('first'), states=[], published=[];
const initial = {id:'draft',revision:0,inputs:input('saved'),target_id:'target',base_fingerprint:'original',state:'active'};
const options = {apiBase:'/api/character-workflows',initialDraft:null,
 collect:()=>clone(local),restore:async i=>{local=clone(i)},
 renderStatus:(s,detail)=>states.push([s,detail]),onPublished:r=>published.push(r)};
require('./static/js/character_drafts.js');
'''
    result = subprocess.run(['node', '-e', harness + '\n(async()=>{\n' + source +
                             '\n})().catch(e=>{console.error(e);process.exitCode=1;});'],
                            cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_serialized_autosave_and_finish():
    node(r'''
let active=0,maxConcurrentSaves=0,calls=[]; const gate=deferred();
global.fetch=async(url,opts)=>{
 const b=JSON.parse(opts.body); calls.push([url,b]); active++; maxConcurrentSaves=Math.max(active,maxConcurrentSaves);
 if(calls.length===1) await gate.promise;
 active--;
 if(url.endsWith('/publish')) return reply({character_id:'pc',url:'/characters/pc',revision:b.expected_revision});
 return reply({draft:{...initial,revision:calls.length-1,inputs:b.inputs}});
};
const c=CharacterDrafts.create(options);
c.changed(); const saving=c.saveNow(); await tick();
assert.equal(states.at(-1)[0],'saving');
local=input('latest'); c.changed(); const finishing=c.finish();
await tick(); assert.equal(calls.length,1); gate.resolve();
await Promise.all([saving,finishing]);
assert.equal(maxConcurrentSaves,1);
assert.equal(calls[1][1].inputs.form.name,'latest');
assert.equal(calls[2][1].expected_revision,1);
assert.equal(published.length,1); assert.equal(states.at(-1)[0],'committed'); c.destroy();
''')


def test_conflict_copy_preserves_base():
    node(r'''
let calls=[];
global.fetch=async(url,opts)=>{
 const b=JSON.parse(opts.body); calls.push([url,b]);
 if(opts.method==='PATCH') return reply({error:{code:'draft_revision_conflict',message:'Conflict'}},409);
 assert.ok(url.endsWith('/draft/copy')); assert.equal(b.inputs.form.name,'local conflict');
 return reply({draft:{...initial,id:'copy',inputs:b.inputs}});
};
const c=CharacterDrafts.create({...options,initialDraft:initial});
await tick(); local=input('local conflict'); c.changed();
await assert.rejects(c.saveNow()); assert.equal(states.at(-1)[0],'conflict');
c.changed(); await tick(); assert.equal(calls.length,1);
await c.copy(); assert.equal(local.form.name,'local conflict');
assert.equal(states.at(-1)[1].draft.base_fingerprint,'original');
assert.equal(states.at(-1)[1].draft.target_id,'target'); c.destroy();
''')


def test_lost_create_save_and_publish_responses_are_reconciled():
    node(r'''
let stored=null,createBodies=[],publishBodies=[],loseCreate=true,loseSave=true,losePublish=true;
global.fetch=async(url,opts={})=>{
 if(!opts.method || opts.method==='GET') return reply({draft:stored});
 const b=JSON.parse(opts.body);
 if(url.endsWith('/publish')) {
   publishBodies.push(b); if(losePublish){losePublish=false;throw new Error('lost');}
   return reply({character_id:'pc',url:'/characters/pc'});
 }
 if(opts.method==='POST') {
   createBodies.push(b); stored={...initial,inputs:b.inputs};
   if(loseCreate){loseCreate=false;throw new Error('lost');}
 } else {
   stored={...stored,revision:stored.revision+1,inputs:b.inputs};
   if(loseSave){loseSave=false;throw new Error('lost');}
 }
 return reply({draft:stored});
};
const c=CharacterDrafts.create(options); c.changed(); await assert.rejects(c.saveNow());
local=input('latest'); c.changed(); await c.saveNow();
assert.deepEqual(createBodies[0],createBodies[1]); assert.equal(stored.inputs.form.name,'latest');
await assert.rejects(c.finish()); await c.finish();
assert.deepEqual(publishBodies[0],publishBodies[1]); assert.equal(published.length,1); c.destroy();
''')


def test_permission_loss_and_stale_response_preserve_form():
    node(r'''
const gate=deferred(); global.fetch=async()=>{await gate.promise;return reply({draft:{...initial,inputs:input('stale')}});};
const c=CharacterDrafts.create(options); c.changed(); const saving=c.saveNow(); await tick();
const replacement={...initial,id:'new',inputs:input('replacement')};
await c.resume(replacement); gate.resolve(); await saving;
assert.equal(local.form.name,'replacement'); assert.equal(states.at(-1)[1].draft.id,'new');
let writes=0; global.fetch=async()=>{writes++;return reply({error:{code:'forbidden',message:'Access ended'}},403);};
local=input('keep this'); c.changed(); await assert.rejects(c.saveNow());
assert.equal(states.at(-1)[0],'forbidden'); c.changed(); await assert.rejects(c.saveNow());
assert.equal(writes,1); assert.equal(local.form.name,'keep this'); c.destroy();
''')


def test_lost_save_reconciliation_ignores_json_key_order():
    node(r'''
let reads=0;
global.fetch=async(url,opts)=>{
 if(opts.method==='PATCH') throw new Error('lost');
 reads++; const i=clone(local);
 return reply({draft:{...initial,revision:1,inputs:{submission:i.submission,ui:i.ui,
    form:i.form,payload_version:i.payload_version,kind:i.kind}}});
};
const c=CharacterDrafts.create({...options,initialDraft:initial}); await tick();
local=input('changed'); c.changed(); await c.saveNow();
assert.equal(reads,1); assert.equal(states.at(-1)[0],'saved'); c.destroy();
''')


def test_debounce_and_navigation_warning_wait_for_acknowledgement():
    node(r'''
let scheduled=null,delay=null,writes=0;const gate=deferred();
global.setTimeout=(fn,ms)=>{scheduled=fn;delay=ms;return 1;};global.clearTimeout=()=>{scheduled=null;};
global.fetch=async(url,opts)=>{writes++;await gate.promise;return reply({draft:{...initial,inputs:JSON.parse(opts.body).inputs}});};
const c=CharacterDrafts.create(options);c.changed();local=input('second');c.changed();
assert.equal(delay,750);assert.equal(writes,0);
let warned=false;events.beforeunload({preventDefault:()=>warned=true});assert.ok(warned);
scheduled();await tick();assert.equal(writes,1);assert.equal(states.at(-1)[0],'saving');
gate.resolve();await tick();assert.equal(states.at(-1)[0],'saved');
warned=false;events.beforeunload({preventDefault:()=>warned=true});assert.equal(warned,false);c.destroy();
''')


def test_restore_failure_is_visible_and_cannot_overwrite_saved_inputs():
    node(r'''
let writes=[];
global.fetch=async(url)=>{writes.push(url);return reply({ok:true});};
const c=CharacterDrafts.create({...options,initialDraft:initial,
 restore:async()=>{throw new Error('Unsupported saved choice');}});
await tick(); assert.equal(states.at(-1)[0],'restore_error');
c.changed(); await assert.rejects(c.saveNow()); await assert.rejects(c.finish());
assert.equal(writes.length,0);
await c.discard(); assert.deepEqual(writes,['/api/character-workflows/drafts/draft/discard']);
''')


def test_publication_freezes_editor_until_definite_response():
    node(r'''
const els=Object.fromEntries(['draft-status','draft-save','draft-copy','draft-reload','draft-discard'].map(id=>
 [id,{dataset:{},focus(){}}]));
const editor={inert:false};
document.getElementById=id=>els[id];
document.querySelectorAll=selector=>selector==='[data-workflow-editor]'?[editor]:[];
global.location={href:'http://localhost/player/builder'};global.history={replaceState(){}};
let attempt=0;
global.fetch=async()=>{attempt++;if(attempt===1)throw new Error('lost');return reply({error:{message:'Invalid'}},422);};
const c=CharacterDrafts.mount({initialDraft:initial},options.collect,options.restore);
await tick();assert.equal(editor.inert,false);
await assert.rejects(c.finish());assert.equal(editor.inert,true);
assert.equal(els['draft-save'].disabled,false);
assert.equal(els['draft-save'].textContent,'Retry publication');
await assert.rejects(c.finish());assert.equal(editor.inert,false);c.destroy();
''')


def test_resumed_publication_retries_without_saving_or_discarding():
    node(r'''
let calls=[];
global.fetch=async(url,opts)=>{
 calls.push([url,JSON.parse(opts.body)]);
 if(calls.length===1)throw new Error('lost');
 return reply({character_id:'pc',url:'/characters/pc'});
};
const c=CharacterDrafts.create({...options,initialDraft:{...initial,state:'publishing',revision:1}});
await tick(); assert.equal(states.at(-1)[0],'recovery');
await assert.rejects(c.discard());await assert.rejects(c.saveNow());
await assert.rejects(c.finish()); await c.finish();
assert.equal(calls.length,2);assert.ok(calls[0][0].endsWith('/publish'));
assert.deepEqual(calls[0],calls[1]);assert.equal(published.length,1);c.destroy();
''')


def test_recovery_page_reloads_editable_builder_after_proven_nonwrite():
    node(r'''
const els=Object.fromEntries(['draft-status','draft-save','draft-copy','draft-reload','draft-discard'].map(id=>
 [id,{dataset:{},focus(){}}]));
document.getElementById=id=>els[id];document.querySelectorAll=()=>[];
let reloaded=false;
global.location={href:'http://localhost/player/builder',reload(){reloaded=true;}};
global.history={replaceState(){}};global.confirm=()=>true;
global.fetch=async(url,opts)=>opts.method==='GET'?reply({draft:{...initial,revision:2}}):
 reply({error:{code:'publication_retry_ready',message:'Reload the draft and retry.'}},409);
const c=CharacterDrafts.mount({recovering:true,initialDraft:{...initial,state:'publishing',revision:1}},
 options.collect,options.restore);
await tick();await assert.rejects(c.finish());
await els['draft-reload'].onclick();assert.equal(reloaded,true);c.destroy();
''')


def test_conflict_reload_of_committed_draft_uses_authorized_server_redirect():
    node(r'''
const els=Object.fromEntries(['draft-status','draft-save','draft-copy','draft-reload','draft-discard'].map(id=>
 [id,{dataset:{},focus(){}}]));
document.getElementById=id=>els[id];document.querySelectorAll=()=>[];
let reloaded=false;
global.location={href:'http://localhost/player/builder',reload(){reloaded=true;}};
global.history={replaceState(){}};global.confirm=()=>true;
global.fetch=async(url,opts)=>opts.method==='GET'?reply({draft:{...initial,inputs:null,state:'committed'}}):
 reply({error:{message:'Draft changed'}},409);
const c=CharacterDrafts.mount({initialDraft:initial},options.collect,options.restore);
await tick();local=input('another tab');c.changed();await assert.rejects(c.saveNow());
await els['draft-reload'].onclick();assert.equal(reloaded,true);c.destroy();
''')
