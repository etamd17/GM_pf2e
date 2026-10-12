// Canvas text must use the app's UI typeface token, never a hardcoded family.
// The legacy map shipped `system-ui` on every label for months because the rule
// lived in CSS and canvas code bypassed it (CLAUDE.md, map rules).
const FALLBACK_UI_FONT = "'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif";

export function uiFont(): string {
  if (typeof document === 'undefined') return FALLBACK_UI_FONT;
  const value = getComputedStyle(document.documentElement).getPropertyValue('--font-ui').trim();
  return value || FALLBACK_UI_FONT;
}
