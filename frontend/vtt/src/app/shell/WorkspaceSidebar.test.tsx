// @vitest-environment jsdom
import { useState } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { WorkspaceSidebar, useSidebarTab } from './WorkspaceSidebar';
import { FirstRunGuidance } from './FirstRunGuidance';
import { SceneBar } from './SceneBar';

afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });
function Harness({member = 'alice'}: {member?: string}) {
  const [tab, onTab] = useSidebarTab(member);
  const [open, setOpen] = useState(true);
  return <WorkspaceSidebar open={open} onToggle={() => setOpen(!open)} tab={tab} onTab={onTab}
    panels={{inspector: <input aria-label="Unfinished label" defaultValue="original" />, creatures: 'Roster', scene: 'Settings', tv: 'Pairing', import: 'Import result'}} />;
}
it('keeps an unfinished field mounted across tab switches and collapse', () => {
  render(<Harness />);
  fireEvent.change(screen.getByLabelText('Unfinished label'), {target: {value: 'unfinished'}});
  fireEvent.click(screen.getByRole('tab', {name: 'TV'}));
  fireEvent.click(screen.getByRole('button', {name: 'Hide sidebar'}));
  fireEvent.click(screen.getByRole('button', {name: 'Show sidebar'}));
  fireEvent.click(screen.getByRole('tab', {name: 'Inspector'}));
  expect((screen.getByLabelText('Unfinished label') as HTMLInputElement).value).toBe('unfinished');
});
it('wraps keyboard focus and selection, including Home and End', () => {
  render(<Harness />);
  fireEvent.keyDown(screen.getByRole('tab', {name: 'Inspector'}), {key: 'ArrowLeft'});
  expect(document.activeElement).toBe(screen.getByRole('tab', {name: 'Import'}));
  expect(document.activeElement?.getAttribute('aria-selected')).toBe('true');
  fireEvent.keyDown(document.activeElement!, {key: 'Home'});
  expect(document.activeElement).toBe(screen.getByRole('tab', {name: 'Inspector'}));
  fireEvent.keyDown(document.activeElement!, {key: 'End'});
  expect(document.activeElement).toBe(screen.getByRole('tab', {name: 'Import'}));
  fireEvent.keyDown(document.activeElement!, {key: 'ArrowRight'});
  expect(document.activeElement).toBe(screen.getByRole('tab', {name: 'Inspector'}));
});
it('remembers a tab for its member without leaking it to another member', () => {
  const view = render(<Harness />);
  fireEvent.click(screen.getByRole('tab', {name: 'Scene'}));
  expect(localStorage.getItem('vtt.sidebar:alice')).toBe('scene');
  view.rerender(<Harness member="bob" />);
  expect(screen.getByRole('tab', {name: 'Inspector'}).getAttribute('aria-selected')).toBe('true');
  view.unmount();
  render(<Harness />);
  expect(screen.getByRole('tab', {name: 'Scene'}).getAttribute('aria-selected')).toBe('true');
});
it('ignores invalid storage and still works when storage throws', () => {
  localStorage.setItem('vtt.sidebar:alice', 'unknown');
  const view = render(<Harness />);
  expect(screen.getByRole('tab', {name: 'Inspector'}).getAttribute('aria-selected')).toBe('true');
  view.unmount();
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
  render(<Harness />);
  fireEvent.click(screen.getByRole('tab', {name: 'TV'}));
  expect(screen.getByRole('tab', {name: 'TV'}).getAttribute('aria-selected')).toBe('true');
});
it('guidance uses explicit create and pairing actions', () => {
  const onNew = vi.fn(), onPair = vi.fn();
  render(<FirstRunGuidance enabled={false} onNew={onNew} onPair={onPair} />);
  fireEvent.click(screen.getByRole('button', {name: 'Create first scene'}));
  fireEvent.click(screen.getByRole('button', {name: 'Pair a TV'}));
  expect(onNew).not.toHaveBeenCalled();
  expect(onPair).toHaveBeenCalledOnce();
});
it('scene navigation does not publish or change the TV, with undo always present', () => {
  const onOpen = vi.fn(), onShow = vi.fn(), onRename = vi.fn();
  render(<SceneBar scenes={[{id:'next', name:'Next room', published:false, onTable:false}]} scene={null}
    fallbackName="Empty" enabled={false} canUndo={false} canRedo={false} nextUndo={null}
    onOpen={onOpen} onShow={onShow} onRename={onRename} onNew={vi.fn()} onClear={vi.fn()} onUndo={vi.fn()} onRedo={vi.fn()}>{null}</SceneBar>);
  fireEvent.change(screen.getByLabelText('Editing'), {target: {value:'next'}});
  expect(onOpen).toHaveBeenCalledWith('next');
  expect(onShow).not.toHaveBeenCalled();
  expect(onRename).not.toHaveBeenCalled();
  expect((screen.getByRole('button', {name:'Undo'}) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', {name:'Redo'}) as HTMLButtonElement).disabled).toBe(true);
});
