// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import type {GmEntity} from '../../protocol/frames';
import {CreaturesPanel} from './CreaturesPanel';
import {TrackerLink} from './TrackerLink';
const actor=(id:string,pc=false)=>({kind:'actor',id,name:pc ? 'Valeros' : 'Ogre',version:'vAAAAAAAAAAAAAAAAAAAAAAAA',pc,hp:12,maxHp:20,size:pc ? 1 : 2,sheetUrl:null,hidden:false,active:false,status:'',conditions:[]}) as Extract<GmEntity,{kind:'actor'}>;
const props=()=>({entities:[actor('chr_val',true),actor('cmb_ogre')],enabled:true,pendingActorIds:new Set<string>(),onPlace:vi.fn(),onDragStart:vi.fn(),onDragEnd:vi.fn()});
afterEach(()=>{cleanup();vi.restoreAllMocks();});
it('lists party and encounter separately with size, HP and only resolved sheet links',()=>{
  const p=props();render(<CreaturesPanel {...p} entities={[{...actor('chr_val',true),sheetUrl:'/player/sheet/val'},actor('cmb_ogre')]}/>);
  expect(within(screen.getByRole('region',{name:'Party'})).getByText('Valeros')).toBeTruthy();
  expect(within(screen.getByRole('region',{name:'Current encounter'})).getByText('Ogre')).toBeTruthy();
  expect(screen.getByRole('link',{name:'Open sheet for Valeros'}).getAttribute('href')).toBe('/player/sheet/val');
  expect(screen.queryByRole('link',{name:'Open sheet for Ogre'})).toBeNull();
  expect(screen.getByText('Size 2 × 2 squares')).toBeTruthy();
});
it('Place is a native button, and drag only passes the actor identity to the workspace',()=>{
  const p=props();render(<CreaturesPanel {...p}/>);const button=screen.getByRole('button',{name:'Place Ogre (encounter)'});
  fireEvent.click(button,{detail:0});expect(p.onPlace).toHaveBeenCalledWith('cmb_ogre');expect(button.getAttribute('draggable')).toBe('true');
  const transfer={setData:vi.fn()};fireEvent.dragStart(button,{dataTransfer:transfer});expect(p.onDragStart).toHaveBeenCalledWith('cmb_ogre',expect.anything());
  fireEvent.dragEnd(button);expect(p.onDragEnd).toHaveBeenCalledOnce();
});
it('marks existing tokens, blocks pending placement, and removes stale actor rows with the mirror',()=>{
  const p=props();const token={kind:'token',id:'tok_a',actorId:'cmb_ogre'} as GmEntity;
  const view=render(<CreaturesPanel {...p} entities={[...p.entities,token]} pendingActorIds={new Set(['cmb_ogre'])}/>);
  expect(screen.getByText('On this map (1)')).toBeTruthy();expect(screen.getByText('Placing…')).toBeTruthy();
  expect((screen.getByRole('button',{name:'Place Ogre (encounter)'}) as HTMLButtonElement).disabled).toBe(true);
  view.rerender(<CreaturesPanel {...p} entities={[actor('chr_val',true)]}/>);expect(screen.queryByText('Ogre')).toBeNull();
});
it('offline keeps the list readable but prevents starting a placement or drag',()=>{
  const p=props();render(<CreaturesPanel {...p} enabled={false}/>);const button=screen.getByRole('button',{name:'Place Valeros (party)'}) as HTMLButtonElement;
  expect(button.disabled).toBe(true);expect(button.draggable).toBe(false);
});
it('Open tracker posts campaign activation with CSRF and then=/tracker to a new tab',()=>{
  render(<TrackerLink boot={{campaignId:'camp_a',csrf:'proof'}}/>);
  const form=screen.getByRole('button',{name:'Open tracker'}).closest('form')!;
  expect(form.getAttribute('method')).toBe('post');expect(form.getAttribute('action')).toBe('/campaign/camp_a/activate');expect(form.target).toBe('_blank');
  const data=new FormData(form);expect(data.get('_csrf')).toBe('proof');expect(data.get('then')).toBe('/tracker');
  expect(data.has('takeover')).toBe(false);expect(form.getAttribute('rel')).toContain('noopener');expect(form.getAttribute('rel')).not.toContain('noreferrer');
});
