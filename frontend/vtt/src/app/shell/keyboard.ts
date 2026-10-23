import type { Tool } from './ToolRail';

export const TOOL_KEYS: Readonly<Record<string, Tool>> = {
  v: 'select', p: 'token', m: 'ruler', a: 'template', w: 'wall', f: 'fog-reveal', r: 'terrain',
};

// Shared by the map callback and DOM shortcuts. Native text editing and IME
// always win, including editable ancestors of the event target.
export function ignoresMapKey(e: KeyboardEvent): boolean {
  const target = e.target;
  return e.isComposing || e.keyCode === 229 || e.repeat || e.ctrlKey || e.metaKey || e.altKey ||
    (target instanceof Element && !!target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])'));
}

export function hasMapToolFocus(target: EventTarget | null): boolean {
  return target instanceof Element && !!target.closest('.vtt-map-host, .vtt-rail, .vtt-token-selection, [aria-label="Tokens on the map"]');
}
