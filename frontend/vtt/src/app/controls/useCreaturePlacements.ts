import {useCallback,useEffect,useRef,useState} from 'react';
import type {GmEntity} from '../../protocol/frames';
import {isTerminal,type ConnectionStatus} from '../../state/connection';
import {useCommands} from '../../state/commands';
interface Placement {actorId:string;sceneId:string;tokenId:string;commandId:string}
// A receipt alone does not make the token visible. Keep Place disabled until
// the token lands in the mirror, or until this specific command is rejected.
export function useCreaturePlacements(entities:ReadonlyMap<string,GmEntity>,status:ConnectionStatus) {
  const held=useRef(new Map<string,Placement>());
  const [pendingActorIds,setIds]=useState<ReadonlySet<string>>(new Set());
  const publish=useCallback(()=>setIds(new Set(held.current.keys())),[]);
  const register=useCallback((p:Placement)=>{held.current.set(p.actorId,p);publish();},[publish]);
  useEffect(()=>useCommands.subscribe(state=>{
    if(state.notice?.kind !== 'rejected') return;
    for(const [id,p] of held.current) if(p.commandId === state.notice.commandId) {held.current.delete(id);publish();}
  }),[publish]);
  useEffect(()=>{
    let changed=false;
    for(const [id,p] of held.current) if(isTerminal(status) || [...entities.values()].some(e=>e.kind==='token' && e.id===p.tokenId)) {held.current.delete(id);changed=true;}
    if(changed) publish();
  },[entities,status,publish]);
  return {pendingActorIds,register,isPending:(id:string)=>held.current.has(id)};
}
