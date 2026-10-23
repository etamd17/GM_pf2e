import type { GmEntity } from '../../protocol/frames';
import { createToken, moveToken, newEntityId, type Draft } from '../gmCommands';
export type GroupToken = Extract<GmEntity,{kind:'token'}>;
type Scene = Pick<Extract<GmEntity,{kind:'scene'}>,'width'|'height'|'gridSize'>;

// One common, whole-square displacement preserves formation at scene edges.
// Locked tokens stay where they are; hidden tokens keep their hidden flag.
export function groupMoves(tokens:readonly GroupToken[],scene:Scene,dx:number,dy:number):Draft[] {
  const movable=tokens.filter(t=>!t.locked);
  if(!movable.length) return [];
  const delta=(wanted:number,axis:'x'|'y',edge:number)=>{
    const low=Math.max(...movable.map(t=>Math.min(t.size*scene.gridSize/2,edge)-t[axis]));
    const high=Math.min(...movable.map(t=>Math.max(edge-t.size*scene.gridSize/2,0)-t[axis]));
    if(low>high) return 0;
    return Math.max(Math.ceil(low/scene.gridSize),Math.min(Math.floor(high/scene.gridSize),Math.round(wanted/scene.gridSize)))*scene.gridSize;
  };
  const x=delta(dx,'x',scene.width),y=delta(dy,'y',scene.height);
  return x || y ? movable.map(t=>moveToken(t,t.x+x,t.y+y)) : [];
}

export function pasteTokens(tokens:readonly GroupToken[],scene:Scene):Draft[] {
  // Copies get fresh ids and preserve the source's authored token fields;
  // tracker links are retained, not duplicated as tracker creatures.
  const copies=tokens.map(t=>({...t,locked:false}));
  const moves=groupMoves(copies,scene,scene.gridSize,scene.gridSize);
  const positions=new Map(moves.map(d=>[String(d.payload.id),d.payload]));
  return tokens.map(t=>{
    const position=positions.get(t.id);
    const draft=createToken(newEntityId('tok'),{x:Number(position?.x ?? t.x),y:Number(position?.y ?? t.y)},t.label,t.color,t);
    return {...draft,payload:{...draft.payload,...(t.vision !== undefined ? {vision:t.vision} : {}),...(t.order !== undefined ? {order:t.order} : {})}};
  });
}
