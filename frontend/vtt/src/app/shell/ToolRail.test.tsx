// @vitest-environment jsdom
import { useState } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ToolRail, type Tool } from './ToolRail';
afterEach(cleanup);
function Harness({enabled = true, reset = vi.fn()}: {enabled?:boolean; reset?:()=>void}) {
  const [tool,setTool] = useState<Tool>('select');
  const [terrain,setTerrain] = useState(1);
  const [open,setOpen] = useState(true);
  return <ToolRail railOpen={open} onToggle={() => setOpen(!open)} tool={tool} onChoose={setTool}
    inputEnabled={enabled} templateShape="burst" templateSize={3} setTemplateShape={vi.fn()} setTemplateSize={vi.fn()}
    terrainKind={terrain} setTerrainKind={setTerrain} canResetFog onResetFog={reset} />;
}
it('groups the tools and marks the selected group and sub-tool', () => {
  render(<Harness />);
  for (const group of ['Select','Place','Measure','Templates','Walls','Fog','Terrain']) {
    expect(screen.getByRole('button', {name:group})).toBeTruthy();
  }
  fireEvent.click(screen.getByRole('button', {name:'Place'}));
  expect(screen.getByRole('button', {name:'Token'}).getAttribute('aria-pressed')).toBe('true');
  fireEvent.click(screen.getByRole('button', {name:'Light'}));
  expect(screen.getByRole('button', {name:'Place'}).getAttribute('aria-pressed')).toBe('true');
  expect(screen.getByRole('button', {name:'Light'}).getAttribute('aria-pressed')).toBe('true');
  expect(screen.getByRole('button', {name:'Token'}).getAttribute('aria-pressed')).toBe('false');
  fireEvent.click(screen.getByRole('button', {name:'Measure'}));
  expect(screen.queryByRole('button', {name:'Token'})).toBeNull();
  fireEvent.click(screen.getByRole('button', {name:'Ping'}));
  expect(screen.getByRole('button', {name:'Ping'}).getAttribute('aria-pressed')).toBe('true');
});
it('provides door, fog actions and distinct paint/erase selection', () => {
  const reset = vi.fn();
  render(<Harness reset={reset} />);
  fireEvent.click(screen.getByRole('button', {name:'Walls'}));
  fireEvent.click(screen.getByRole('button', {name:'Door'}));
  expect(screen.getByRole('button', {name:'Door'}).getAttribute('aria-pressed')).toBe('true');
  fireEvent.click(screen.getByRole('button', {name:'Fog'}));
  fireEvent.click(screen.getByRole('button', {name:'Hide'}));
  expect(screen.getByRole('button', {name:'Hide'}).getAttribute('aria-pressed')).toBe('true');
  fireEvent.click(screen.getByRole('button', {name:'Reset fog'}));
  expect(reset).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', {name:'Terrain'}));
  fireEvent.click(screen.getByRole('button', {name:'Erase terrain'}));
  expect((screen.getByLabelText('Paint') as HTMLSelectElement).value).toBe('0');
  expect(screen.getByRole('button', {name:'Erase terrain'}).getAttribute('aria-pressed')).toBe('true');
  fireEvent.click(screen.getByRole('button', {name:'Paint terrain'}));
  expect((screen.getByLabelText('Paint') as HTMLSelectElement).value).toBe('1');
});
it('keeps selection available offline and disables every editing group', () => {
  render(<Harness enabled={false} />);
  for (const label of ['Place','Measure','Templates','Walls','Fog','Terrain']) {
    expect((screen.getByRole('button', {name:label}) as HTMLButtonElement).disabled).toBe(true);
  }
  expect((screen.getByRole('button', {name:'Select'}) as HTMLButtonElement).disabled).toBe(false);
});
it('restores the last non-zero terrain kind after erasing, including after changing groups', () => {
  render(<Harness />);
  fireEvent.click(screen.getByRole('button', {name:'Terrain'}));
  fireEvent.change(screen.getByLabelText('Paint'), {target:{value:'3'}});
  fireEvent.click(screen.getByRole('button', {name:'Paint terrain'}));
  expect((screen.getByLabelText('Paint') as HTMLSelectElement).value).toBe('3');
  fireEvent.click(screen.getByRole('button', {name:'Erase terrain'}));
  expect((screen.getByLabelText('Paint') as HTMLSelectElement).value).toBe('0');
  fireEvent.click(screen.getByRole('button', {name:'Place'}));
  fireEvent.click(screen.getByRole('button', {name:'Terrain'}));
  fireEvent.click(screen.getByRole('button', {name:'Paint terrain'}));
  expect((screen.getByLabelText('Paint') as HTMLSelectElement).value).toBe('3');
});
it('keeps labels visible, icons decorative and hints explicit about keyboard activation', () => {
  render(<Harness />);
  const group = screen.getByRole('button', {name:'Place'});
  expect(group.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
  expect(within(group).getByText('Place')).toBeTruthy();
  expect(within(group).getByRole('tooltip', {hidden:true}).textContent).toContain('Enter or Space');
  fireEvent.click(screen.getByRole('button', {name:'Hide tools'}));
  expect(screen.queryByRole('button', {name:'Place'})).toBeNull();
  fireEvent.click(screen.getByRole('button', {name:'Tools'}));
  expect(screen.getByRole('button', {name:'Select'}).getAttribute('aria-pressed')).toBe('true');
});

it('shows a hint outside the scrolling rail on focus and dismisses it on Escape or blur', () => {
  render(<Harness />);
  const button = screen.getByRole('button', {name:'Place'});
  fireEvent.focus(button);
  const hint = document.querySelector('.vtt-floating-tool-hint');
  expect(hint?.textContent).toContain('Place: Enter or Space');
  expect(screen.getByRole('navigation', {name:'Map tools'}).contains(hint)).toBe(false);
  fireEvent.keyDown(button,{key:'Escape'});
  expect(document.querySelector('.vtt-floating-tool-hint')).toBeNull();
  fireEvent.focus(button);
  fireEvent.blur(button);
  expect(document.querySelector('.vtt-floating-tool-hint')).toBeNull();
});
