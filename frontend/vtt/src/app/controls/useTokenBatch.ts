import { useEffect, useRef, useState } from 'react';
import { useCommands } from '../../state/commands';
import { isTerminal, type ConnectionStatus } from '../../state/connection';
import type { Draft } from '../gmCommands';
export interface BatchRow {name:string;id:string;result:string}
export function useTokenBatch(status:ConnectionStatus) {
  const current=useRef<BatchRow[]>([]);
  const [rows,setRows]=useState<BatchRow[]>([]);
  const publish=()=>setRows(current.current.map(r=>({...r})));
  useEffect(()=>useCommands.subscribe(state=>{
    const outcome=state.notice;
    let changed=false;
    for(const row of current.current) {
      if(row.result!=='Waiting') continue;
      if(row.id===state.lastApplied) {row.result='Applied';changed=true;}
      else if(outcome?.kind==='rejected' && row.id===outcome.commandId) {row.result=`Rejected: ${outcome.code}`;changed=true;}
    }
    if(changed) publish();
  }),[]);
  useEffect(()=>{
    if(isTerminal(status)) {
      for(const row of current.current) if(row.result==='Waiting') row.result='Unknown: connection ended; check the scene';
      publish();
    }
  },[status]);
  const busy=()=>current.current.some(r=>r.result==='Waiting');
  return {rows,busy:busy(),run(items:readonly {draft:Draft;name:string}[],skipped:readonly string[],issue:(d:Draft)=>string|null) {
    if(busy()) return false;
    current.current=skipped.map(name=>({name,id:'',result:'Skipped: locked or missing'}));
    for(const item of items) {
      const row={name:item.name,id:'',result:'Waiting'};current.current.push(row);
      try {const id=issue(item.draft);row.id=id ?? '';if(!id) row.result='Not queued';}
      catch {row.result='Unknown: issue failed; check the scene before retrying';}
    }
    publish();return true;
  }};
}
