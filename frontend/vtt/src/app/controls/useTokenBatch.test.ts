// @vitest-environment jsdom
import { act,cleanup,renderHook } from '@testing-library/react';
import { afterEach,beforeEach,expect,it,vi } from 'vitest';
import { useTokenBatch } from './useTokenBatch';
import { useCommands } from '../../state/commands';
import type { ConnectionStatus } from '../../state/connection';
beforeEach(()=>useCommands.setState({lastApplied:null,notice:null}));afterEach(cleanup);
const items=[{name:'A',draft:{type:'token.move',expected:[],payload:{id:'tok_a',x:1,y:1}}},{name:'B',draft:{type:'token.move',expected:[],payload:{id:'tok_b',x:2,y:1}}}] as const;
it('keeps every exact outcome, including partial rejections in either order; never retries',()=>{
  const view=renderHook(()=>useTokenBatch('live'));let n=0;const issue=vi.fn(()=>`cmd_${++n}`);
  act(()=>{view.result.current.run(items,['Locked'],issue);view.result.current.run(items,[],issue);});
  expect(issue).toHaveBeenCalledTimes(2);expect(view.result.current.busy).toBe(true);
  act(()=>useCommands.getState().notify({kind:'rejected',commandId:'cmd_2',code:'stale'} as never));
  act(()=>useCommands.getState().notify({kind:'applied',commandId:'cmd_1'} as never));
  expect(view.result.current.rows.map(r=>r.result)).toEqual(['Skipped: locked or missing','Applied','Rejected: stale']);
  expect(view.result.current.busy).toBe(false);expect(issue).toHaveBeenCalledTimes(2);
});
it('distinguishes unqueued and unknown outcomes without stopping other tokens',()=>{
  const view=renderHook(()=>useTokenBatch('live'));const issue=vi.fn().mockReturnValueOnce(null).mockImplementationOnce(()=>{throw Error('after send');});
  act(()=>{view.result.current.run(items,[],issue);});
  expect(view.result.current.rows[0]?.result).toBe('Not queued');expect(view.result.current.rows[1]?.result).toMatch(/^Unknown/);
});
it('marks remaining results unknown when access ends rather than claiming rollback',()=>{
  const view=renderHook(({status}:{status:ConnectionStatus})=>useTokenBatch(status),{initialProps:{status:'live' as ConnectionStatus}});
  act(()=>{view.result.current.run(items,[],()=> 'cmd_one');});view.rerender({status:'revoked'});
  expect(view.result.current.rows.every(r=>r.result.startsWith('Unknown'))).toBe(true);
});
