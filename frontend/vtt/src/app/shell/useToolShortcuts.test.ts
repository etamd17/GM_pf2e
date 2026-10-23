// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useToolShortcuts } from './useToolShortcuts';

beforeEach(() => localStorage.clear());
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('remembers disabling and re-enabling after remount, separately for each member', () => {
  const first = renderHook(() => useToolShortcuts('gm_a'));
  expect(first.result.current[0]).toBe(true);
  act(() => first.result.current[1](false));
  first.unmount();
  const next = renderHook(({member}) => useToolShortcuts(member), {initialProps:{member:'gm_a'}});
  expect(next.result.current[0]).toBe(false);
  next.rerender({member:'gm_b'});
  expect(next.result.current[0]).toBe(true);
  next.rerender({member:'gm_a'});
  expect(next.result.current[0]).toBe(false);
  act(() => next.result.current[1](true));
  next.unmount();
  expect(renderHook(() => useToolShortcuts('gm_a')).result.current[0]).toBe(true);
});

it('works in page memory when storage reads/writes throw and does not persist without a member', () => {
  vi.spyOn(Storage.prototype,'getItem').mockImplementation(() => {throw new Error('disabled');});
  const write = vi.spyOn(Storage.prototype,'setItem').mockImplementation(() => {throw new Error('disabled');});
  const view = renderHook(({member}: {member:string | undefined}) => useToolShortcuts(member), {initialProps:{member:'gm_a' as string | undefined}});
  expect(view.result.current[0]).toBe(true);
  act(() => view.result.current[1](false));
  expect(view.result.current[0]).toBe(false);
  view.rerender({member:'gm_b'});
  expect(view.result.current[0]).toBe(true);
  view.rerender({member:undefined});write.mockClear();
  act(() => view.result.current[1](false));
  expect(view.result.current[0]).toBe(false);expect(write).not.toHaveBeenCalled();
});
