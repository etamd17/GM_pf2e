// @vitest-environment jsdom
import { ViewportButtons } from './ViewportButtons';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ToolRail } from './ToolRail';
import { EncounterLine } from './EncounterLine';
import { WorkspaceStatus } from './WorkspaceStatus';

afterEach(cleanup);

it('keeps selection available offline while disabling editing tools', () => {
  const choose = vi.fn();
  render(<ToolRail railOpen tool="select" inputEnabled={false} templateShape="burst" templateSize={3} terrainKind={1}
    onToggle={vi.fn()} onChoose={choose} setTemplateShape={vi.fn()} setTemplateSize={vi.fn()} setTerrainKind={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Token' }));
  expect(choose).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Select' }));
  expect(choose).toHaveBeenCalledWith('select');
});

it('passes a template shape change through the supplied boundary', () => {
  const shape = vi.fn();
  render(<ToolRail railOpen tool="template" inputEnabled templateShape="burst" templateSize={3} terrainKind={1}
    onToggle={vi.fn()} onChoose={vi.fn()} setTemplateShape={shape} setTemplateSize={vi.fn()} setTerrainKind={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Shape'), { target: { value: 'cone' } });
  expect(shape).toHaveBeenCalledWith('cone');
});

it('reports pending sends and zoom without owning the command store', () => {
  render(<WorkspaceStatus pending={[{state:'queued'}]} zoom={0.75} status="reconnecting" />);
  expect(screen.getByText('Waiting to send (1)')).toBeTruthy();
  expect(screen.getByLabelText('Zoom level').textContent).toContain('75%');
  expect(screen.getByText('reconnecting')).toBeTruthy();
});

it('does not invent a turn when no encounter exists', () => {
  render(<EncounterLine actors={[]} />);
  expect(screen.getByText('No encounter running.')).toBeTruthy();
});


it('routes visible viewport buttons to the published API and disables them without a map', () => {
  const controls = { zoomIn: vi.fn(), zoomOut: vi.fn(), fit: vi.fn() };
  const view = render(<ViewportButtons controls={controls} enabled />);
  fireEvent.click(screen.getByRole('button', {name: 'Zoom in'}));
  fireEvent.click(screen.getByRole('button', {name: 'Zoom out'}));
  fireEvent.click(screen.getByRole('button', {name: 'Fit'}));
  expect(controls.zoomIn).toHaveBeenCalledTimes(1);
  expect(controls.zoomOut).toHaveBeenCalledTimes(1);
  expect(controls.fit).toHaveBeenCalledTimes(1);
  view.rerender(<ViewportButtons controls={controls} enabled={false} />);
  fireEvent.click(screen.getByRole('button', {name: 'Zoom in'}));
  expect(controls.zoomIn).toHaveBeenCalledTimes(1);
});
