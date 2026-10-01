(function(root){
  'use strict';
  root.CharacterImports={mount(builderUrl){
    const el=id=>document.getElementById('workflow-import-'+id);
    let attempt=null;
    el('prepare').onclick=async()=>{
      const button=el('prepare'),status=el('status');
      button.disabled=true;
      try {
        if(!attempt){
          const file=el('file').files[0], pasted=el('paste')?.value.trim();
          if(file && pasted) throw new Error('Choose a file or pasted JSON, not both.');
          if(!file && !pasted) throw new Error('Choose a character file or paste JSON first.');
          const key=CharacterDrafts.requestKey(),target=el('target').value;
          if(file){
            const body=new FormData();body.append('file',file);body.append('request_key',key);
            if(target) body.append('target_id',target);
            attempt={body,multipart:true};
          } else attempt={body:{source:JSON.parse(pasted),request_key:key,target_id:target||null},multipart:false};
        }
        status.textContent='Preparing private preview…';
        let data;
        if(attempt.multipart){
          const response=await fetch('/api/character-workflows/imports',{method:'POST',body:attempt.body});
          data=await response.json();
          if(!response.ok){const error=new Error(data.error?.message||'Import failed.');error.status=response.status;throw error;}
        } else data=await CharacterDrafts.request('/api/character-workflows/imports','POST',attempt.body);
        status.textContent='Private preview saved.';
        location.assign(builderUrl+'?draft_id='+encodeURIComponent(data.draft.id));
      } catch(error){
        status.textContent=error.message||'Import failed. Retry to recover the same private preview.';
        if(error.status && error.status<500) attempt=null;
        // While the response is uncertain, retry the same file, target and key.
        // Reloading the roster also exposes any successfully prepared draft.
        ['file','paste','target'].forEach(id=>{if(el(id))el(id).disabled=!!attempt;});
        button.textContent=attempt?'Retry preview':'Preview import';
        status.focus();button.disabled=false;
      }
    };
  }};
})(window);
