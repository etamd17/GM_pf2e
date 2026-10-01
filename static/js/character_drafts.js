/* Private draft transport. One writer, acknowledged revisions, explicit recovery. */
(function (root) {
  'use strict';
  const clone = value => JSON.parse(JSON.stringify(value));
  const stable = value => JSON.stringify(value, function (_, item) {
    return item && typeof item==='object' && !Array.isArray(item)
      ? Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])) : item;
  });
  function requestKey() {
    if (root.crypto.randomUUID) return root.crypto.randomUUID();
    const bytes = root.crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
  }
  async function request(url, method, body) {
    const token = document.querySelector('meta[name="csrf-token"]');
    const response = await fetch(url, {method, credentials:'same-origin',
      headers:{'Content-Type':'application/json', 'X-CSRF-Token':token ? token.content : ''},
      ...(body === undefined ? {} : {body:JSON.stringify(body)})});
    let data;
    try { data = await response.json(); } catch (_) { throw new Error('The server response was lost. Retry to check your draft.'); }
    if (!response.ok) {
      const detail = data.error || {};
      const error = new Error(detail.message || 'Your draft could not be saved.');
      error.status = response.status; error.code = detail.code; error.issues = detail.issues || [];
      throw error;
    }
    return data;
  }
  function create({apiBase='/api/character-workflows', initialDraft=null,
                   targetId=null, collect, restore, renderStatus, onPublished}) {
    let draft=null, status='unsaved', dirty=false, timer=null, inflight=null;
    let epoch=0, restoring=false, dead=false, lastInputs=null, ready=Promise.resolve();
    let createAttempt=null, copyAttempt=null, publishAttempt=null, finishing=null;
    const set = (value, error=null) => {
      if (dead) return;
      status=value; renderStatus(value, {draft:clone(draft), message:error && error.message,
                                       issues:error && error.issues || [], pendingPublication:!!publishAttempt});
    };
    const fail = error => set([401,403,404].includes(error.status) ? 'forbidden' :
      error.status===409 ? 'conflict' : 'error', error);
    const writable = () => {
      if (dead || ['forbidden','conflict','committed','restore_error'].includes(status))
        throw new Error('Reload or copy the draft before continuing.');
    };
    function changed() {
      if (restoring || dead || status==='committed') return;
      dirty=true; clearTimeout(timer);
      if (['forbidden','conflict','restore_error'].includes(status) || publishAttempt) return;
      if (status!=='saving' && status!=='publishing') set('unsaved');
      timer=setTimeout(()=>saveNow().catch(()=>{}),750);
    }
    async function drain() {
      const generation=epoch;
      while (!dead && generation===epoch) {
        writable();
        const inputs=clone(collect());
        if (draft && !createAttempt && stable(inputs)===stable(lastInputs)) {
          dirty=false; set('saved'); return draft;
        }
        set('saving');
        let next, sent;
        if (!draft) {
          if (!createAttempt) createAttempt={inputs,request_key:requestKey(),target_id:targetId};
          sent=createAttempt.inputs;
          next=(await request(apiBase+'/drafts','POST',createAttempt)).draft;
        } else {
          sent=inputs;
          const revision=draft.revision, id=draft.id;
          try {
            next=(await request(apiBase+'/drafts/'+id,'PATCH',
              {inputs:sent,expected_revision:revision})).draft;
          } catch (error) {
            // A lost PATCH response can mean the CAS succeeded. Only adopt its
            // exact acknowledged snapshot; never replace the local form.
            if (error.status) throw error;
            const observed=(await request(apiBase+'/drafts/'+id,'GET')).draft;
            if (observed.revision===revision+1 && stable(observed.inputs)===stable(sent)) next=observed;
            else if (observed.revision!==revision) {
              error.status=409; error.message='Another tab changed this draft. Reload or save a copy.'; throw error;
            } else throw error;
          }
        }
        if (dead || generation!==epoch) return draft;
        draft=next; lastInputs=clone(sent); createAttempt=null;
        // collect again: changes during the request must be saved before Finish.
        if (stable(collect())===stable(lastInputs)) {
          dirty=false; set('saved'); return draft;
        }
      }
      return draft;
    }
    async function saveNow() {
      clearTimeout(timer); await ready; writable();
      if (publishAttempt) throw new Error('Retry publication before editing this draft.');
      if (inflight) return inflight;
      const generation=epoch;
      inflight=drain().catch(error=>{if(generation===epoch) fail(error);throw error;})
        .finally(()=>{inflight=null;});
      return inflight;
    }
    async function finish({force=false}={}) {
      if (finishing) return finishing;
      finishing=(async()=>{
        await ready; writable(); clearTimeout(timer);
        if (!publishAttempt) {
          await saveNow();
          publishAttempt={expected_revision:draft.revision,request_key:requestKey(),force};
        }
        set('publishing');
        try {
          const result=await request(apiBase+'/drafts/'+draft.id+'/publish','POST',publishAttempt);
          dirty=false; set('committed'); onPublished(result); return result;
        } catch (error) {
          // A definite validation failure is editable. An uncertain response
          // retains the same request key/revision until retry reconciles it.
          if (error.status && error.status<500) publishAttempt=null;
          fail(error); throw error;
        }
      })().finally(()=>{finishing=null;});
      return finishing;
    }
    async function resume(next) {
      ++epoch; clearTimeout(timer); restoring=true;
      draft=clone(next); createAttempt=copyAttempt=publishAttempt=null; dirty=false;
      try {
        await restore(clone(draft.inputs)); lastInputs=clone(collect());
        if(draft.state==='publishing') {
          publishAttempt={expected_revision:draft.revision,request_key:requestKey(),force:false};
          set('recovery');
        } else set('saved');
      }
      catch(error) {set('restore_error',new Error('This draft could not be restored. Reload the page or discard it; the saved inputs are unchanged.'));throw error;}
      finally { restoring=false; }
    }
    async function copy() {
      await ready; clearTimeout(timer);
      if (dead || ['forbidden','restore_error'].includes(status) || !draft || publishAttempt) throw new Error('This draft cannot be copied now.');
      if (inflight) await inflight.catch(()=>{});
      if (!copyAttempt) copyAttempt={inputs:clone(collect()),request_key:requestKey()};
      try {
        const next=(await request(apiBase+'/drafts/'+draft.id+'/copy','POST',copyAttempt)).draft;
        draft=next; lastInputs=clone(copyAttempt.inputs); copyAttempt=null;
        dirty=stable(collect())!==stable(lastInputs); set(dirty?'unsaved':'saved');
        return draft;
      } catch(error) {fail(error);throw error;}
    }
    async function discard() {
      await ready; if(status!=='restore_error') writable(); clearTimeout(timer);
      if (publishAttempt) throw new Error('Retry publication before discarding.');
      if (inflight) await inflight;
      if (draft) await request(apiBase+'/drafts/'+draft.id+'/discard','POST',{expected_revision:draft.revision});
      dirty=false; destroy();
    }
    function beforeUnload(event) {
      if (dirty || inflight || publishAttempt) {event.preventDefault();event.returnValue='';}
    }
    function destroy() {dead=true;++epoch;clearTimeout(timer);root.removeEventListener('beforeunload',beforeUnload);}
    root.addEventListener('beforeunload',beforeUnload);
    if (initialDraft) ready=resume(initialDraft).catch(()=>{}); else set('unsaved');
    return {changed,saveNow,finish,discard,copy,resume,destroy};
  }
  function mount(config, collect, restore) {
    const el=id=>document.getElementById(id);
    const labels={unsaved:'Unsaved changes',saving:'Saving…',saved:'Saved',conflict:'Conflict — your local changes are preserved.',
      error:'Could not save — your local changes are preserved.',forbidden:'Access ended — your local changes are preserved.',
      restore_error:'Could not restore draft.',recovery:'Publication needs recovery — retry to check its result.',
      publishing:'Publishing…',committed:'Character published'};
    let controller, currentDraft=config.initialDraft, pendingPublication=false;
    const run = action => Promise.resolve().then(action).catch(error=>{
      el('draft-status').textContent=error.message || 'Unable to complete that action.';
      el('draft-status').focus();
    });
    controller=create({initialDraft:config.initialDraft,targetId:config.targetId,collect,restore,
      renderStatus(state, detail) {
        pendingPublication=detail.pendingPublication;
        currentDraft=detail.draft;
        el('draft-status').dataset.state=state;
        el('draft-status').textContent=labels[state]+(detail.message?' '+detail.message:'')+
          (detail.issues.length?' '+detail.issues.join(' '):'');
        const blocked=['forbidden','committed'].includes(state);
        // Freeze the captured snapshot until publication is definite. Retry
        // remains outside these regions, including after a lost response.
        document.querySelectorAll('[data-workflow-editor]').forEach(region=>{
          region.inert=pendingPublication || state==='committed';
        });
        el('draft-save').disabled=blocked || ['conflict','publishing','restore_error'].includes(state);
        el('draft-save').textContent=pendingPublication?'Retry publication':'Save draft';
        el('draft-copy').hidden=state!=='conflict' || pendingPublication;
        el('draft-reload').hidden=state!=='conflict';
        el('draft-discard').disabled=blocked || state==='publishing' || pendingPublication;
        // Existing finish controls retain their rules gating; only remove their
        // authority here. A successful reload restores the page's normal gate.
        if(blocked || state==='restore_error') document.querySelectorAll('[data-workflow-finish]').forEach(b=>{b.disabled=true;b.hidden=true;});
        if(detail.draft && !blocked) {
          const url=new URL(location.href);url.searchParams.delete('pc');url.searchParams.delete('target_id');
          url.searchParams.set('draft_id',detail.draft.id);history.replaceState(null,'',url);
        }
      }, onPublished:result=>{location.assign(result.url);}});
    el('draft-save').onclick=()=>run(()=>pendingPublication?controller.finish():controller.saveNow());
    el('draft-copy').onclick=()=>run(()=>controller.copy());
    el('draft-reload').onclick=()=>run(async()=>{
      if(!confirm('Replace these local changes with the saved draft?')) return;
      const next=await request('/api/character-workflows/drafts/'+currentDraft.id,'GET');
      if(next.draft.state==='committed' || (config.recovering && next.draft.state==='active')) {
        location.reload();return;
      }
      await controller.resume(next.draft);
    });
    el('draft-discard').onclick=()=>run(async()=>{
      if(!confirm('Discard this private draft? Your published character will not change.')) return;
      await controller.discard();location.assign(config.cancelUrl);
    });
    return controller;
  }
  root.CharacterDrafts={create,request,requestKey,mount};
})(window);
