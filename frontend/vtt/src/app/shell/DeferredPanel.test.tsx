// @vitest-environment jsdom
import { lazy } from 'react';
import { act,cleanup,fireEvent,render,screen } from '@testing-library/react';
import { afterEach,expect,it,vi } from 'vitest';
import { DeferredPanel } from './DeferredPanel';
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();});

it('defers a hidden chunk, shows loading on first visit, then preserves edits across hide/reopen',async()=>{
  let finish!:(value:{default:()=>React.ReactNode})=>void;
  const load=vi.fn(()=>new Promise<{default:()=>React.ReactNode}>(resolve=>{finish=resolve;}));
  const Panel=lazy(load);
  const ui=(active:boolean)=><section hidden={!active}><DeferredPanel active={active}><Panel/></DeferredPanel></section>;
  const view=render(ui(false));expect(load).not.toHaveBeenCalled();
  view.rerender(ui(true));expect(screen.getByText('Loading panel...')).toBeTruthy();
  await act(async()=>finish({default:()=> <input aria-label="Draft" defaultValue="initial"/>}));
  fireEvent.change(screen.getByLabelText('Draft'),{target:{value:'unfinished'}});
  view.rerender(ui(false));view.rerender(ui(true));
  expect((screen.getByLabelText('Draft') as HTMLInputElement).value).toBe('unfinished');expect(load).toHaveBeenCalledOnce();
});

it('a failed panel chunk leaves the rest of the workspace usable',async()=>{
  vi.spyOn(console,'error').mockImplementation(()=>{});
  const reload=vi.fn();vi.stubGlobal('location',{reload});
  const Broken=lazy(()=>Promise.reject(Error('network')));const click=vi.fn();
  render(<><button onClick={click}>Map tools</button><DeferredPanel active><Broken/></DeferredPanel></>);
  expect((await screen.findByRole('alert')).textContent).toMatch(/The tabletop may have been updated/);
  expect(reload).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:/^Reload$/}));expect(reload).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button',{name:'Map tools'}));expect(click).toHaveBeenCalledOnce();
});
