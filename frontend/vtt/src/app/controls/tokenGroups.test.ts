import { expect,it } from 'vitest';
import { groupMoves,pasteTokens,type GroupToken } from './tokenGroups';
const token={kind:'token',id:'tok_a',version:'v'+'A'.repeat(24),x:96,y:96,size:1,label:'A',color:4,hidden:true,locked:false,artAssetId:'asset_a',actorId:'chr_a',controllerIds:['gm_a'],vision:64,order:5} as GroupToken;
const scene={width:640,height:640,gridSize:64};
it('moves unlocked members by one common delta without changing hidden fields or preconditions',()=>{
  const drafts=groupMoves([token,{...token,id:'tok_b',x:160,locked:true},{...token,id:'tok_c',x:224}],scene,64,0);
  expect(drafts.map(d=>d.payload)).toEqual([{id:'tok_a',x:160,y:96},{id:'tok_c',x:288,y:96}]);
  expect(drafts[0]?.expected).toEqual([{kind:'token',id:token.id,version:token.version}]);
});
it('bounds the entire formation instead of independently clamping its members',()=>{
  expect(groupMoves([token,{...token,id:'tok_b',x:608}],scene,128,0)).toEqual([]);
  const moves=groupMoves([token,{...token,id:'tok_b',x:544}],scene,128,64);
  expect(moves.map(d=>d.payload)).toEqual([{id:'tok_a',x:160,y:160},{id:'tok_b',x:608,y:160}]);
  expect(groupMoves([{...token,locked:true}],scene,64,0)).toEqual([]);
});
it('pastes fresh ids with copied authored fields, hidden and locked semantics intact',()=>{
  const drafts=pasteTokens([{...token,locked:true},{...token,id:'tok_b',x:224}],scene);
  expect(new Set(drafts.map(d=>d.payload.id)).size).toBe(2);
  expect(drafts[0]).toMatchObject({type:'token.create',expected:[],payload:{x:160,y:160,locked:true,hidden:true,actorId:'chr_a',artAssetId:'asset_a',vision:64,order:5,controllerIds:['gm_a']}});
  expect(drafts[0]?.payload.id).not.toBe(token.id);expect(drafts[1]?.payload.x).toBe(288);
});
