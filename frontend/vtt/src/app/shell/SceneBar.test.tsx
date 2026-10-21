// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import type {GmEntity} from '../../protocol/frames';
import {SceneBar} from './SceneBar';
import {editingSceneUrl} from './sceneNavigation';
const scene={kind:'scene',id:'scene_a',name:'Hall',version:'vAAAAAAAAAAAAAAAAAAAAAAAA',published:false} as Extract<GmEntity,{kind:'scene'}>;
const scenes=[{id:'scene_a',name:'Hall',published:false,onTable:false},{id:'scene_b',name:'Crypt',published:true,onTable:true}];
const props=()=>({scene,scenes,fallbackName:'Empty',enabled:true,canUndo:false,canRedo:false,nextUndo:null,
  onOpen:vi.fn(),onNew:vi.fn(),onShow:vi.fn(),onClear:vi.fn(),onRename:vi.fn(),onDelete:vi.fn(),onUndo:vi.fn(),onRedo:vi.fn(),children:null});
afterEach(()=>{cleanup();vi.restoreAllMocks();});
it('labels Editing separately from On TV and navigation only opens the editing scene',()=>{
  const p=props();render(<SceneBar {...p}/>);
  expect(screen.getByLabelText('On TV').textContent).toContain('Crypt');
  fireEvent.change(screen.getByLabelText('Editing'),{target:{value:'scene_b'}});
  expect(p.onOpen).toHaveBeenCalledWith('scene_b');expect(p.onShow).not.toHaveBeenCalled();expect(p.onClear).not.toHaveBeenCalled();
  expect(screen.queryByRole('button',{name:/publish/i})).toBeNull();
});
it('requires confirmation before replacing a different TV scene, with cancellation sending nothing',()=>{
  const p=props();const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);render(<SceneBar {...p}/>);
  fireEvent.click(screen.getByRole('button',{name:'Show on TV'}));expect(confirm).toHaveBeenCalledWith(expect.stringContaining('Crypt'));expect(p.onShow).not.toHaveBeenCalled();
  confirm.mockReturnValue(true);fireEvent.click(screen.getByRole('button',{name:'Show on TV'}));expect(p.onShow).toHaveBeenCalledOnce();
});
it('showing an empty TV needs no replacement confirmation and the badge waits for authoritative data',()=>{
  const p=props();const confirm=vi.spyOn(window,'confirm');const view=render(<SceneBar {...p} scenes={scenes.map(s=>({...s,onTable:false}))}/>);
  fireEvent.click(screen.getByRole('button',{name:'Show on TV'}));expect(p.onShow).toHaveBeenCalledOnce();expect(confirm).not.toHaveBeenCalled();
  expect(screen.getByLabelText('On TV').textContent).toContain('Nothing');
  view.rerender(<SceneBar {...p} scenes={scenes.map(s=>({...s,onTable:s.id===scene.id}))}/>);
  expect(screen.getByLabelText('On TV').textContent).toContain('Hall');
});
it('renames through the scene menu without showing the scene on TV',()=>{
  const p=props();render(<SceneBar {...p}/>);fireEvent.click(screen.getByText('Scene menu'));
  fireEvent.change(screen.getByLabelText('Scene name'),{target:{value:'  New hall  '}});
  fireEvent.click(screen.getByRole('button',{name:'Save name'}));expect(p.onRename).toHaveBeenCalledWith('New hall');expect(p.onShow).not.toHaveBeenCalled();
  expect((screen.getByRole('button',{name:'Delete scene'}) as HTMLButtonElement).disabled).toBe(false);
});
it('unknown TV state disables TV writes; offline disables edits but leaves navigation available',()=>{
  const p=props();const view=render(<SceneBar {...p} scenes={null}/>);
  expect(screen.getByLabelText('On TV').textContent).toContain('Unavailable');
  expect((screen.getByRole('button',{name:'Show on TV'}) as HTMLButtonElement).disabled).toBe(true);
  view.rerender(<SceneBar {...p} enabled={false}/>);
  expect((screen.getByRole('button',{name:'Clear TV'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('Editing'),{target:{value:'scene_b'}});expect(p.onOpen).toHaveBeenCalledWith('scene_b');
});

it('confirms deletion by name, including permanence and retained images, before sending',()=>{
  const p=props();const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);render(<SceneBar {...p}/>);
  fireEvent.click(screen.getByText('Scene menu'));fireEvent.click(screen.getByRole('button',{name:'Delete scene'}));
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('Hall'));expect(confirm.mock.calls[0]?.[0]).toMatch(/cannot be undone/);expect(confirm.mock.calls[0]?.[0]).toMatch(/images stay/);
  expect(p.onDelete).not.toHaveBeenCalled();confirm.mockReturnValue(true);fireEvent.click(screen.getByRole('button',{name:'Delete scene'}));expect(p.onDelete).toHaveBeenCalledOnce();
});
it('protects the scene on TV and offers Clear TV before deleting',()=>{
  const p=props();render(<SceneBar {...p} scenes={[{id:scene.id,name:scene.name,published:true,onTable:true}]}/>);
  fireEvent.click(screen.getByText('Scene menu'));
  expect((screen.getByRole('button',{name:'Delete scene'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button',{name:'Clear TV before deleting'}));expect(p.onClear).toHaveBeenCalledOnce();expect(p.onDelete).not.toHaveBeenCalled();
});

it('scene URLs preserve campaign scope and remove the deleted scene for an empty campaign',()=>{
  expect(editingSceneUrl('https://example.test/vtt/gm?campaignId=camp_a&sceneId=old','next')).toBe('https://example.test/vtt/gm?campaignId=camp_a&sceneId=next');
  expect(editingSceneUrl('https://example.test/vtt/gm?campaignId=camp_a&sceneId=old',null)).toBe('https://example.test/vtt/gm?campaignId=camp_a');
});
