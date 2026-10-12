// Canvas text must use the app's UI typeface token, never a hardcoded family.
// The legacy map shipped `system-ui` on every label for months because the rule
// lived in CSS and canvas code bypassed it (CLAUDE.md, map rules).
const FALLBACK_UI_FONT = "'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif";

// The weight and size canvas labels use. Keep in sync with SceneRuntime.
export const CANVAS_LABEL_FONT = { weight: '600', sizePx: 13 } as const;

// Canvas text is rasterised once, when the Text object is built. If the face
// has not loaded by then, the label is drawn in a fallback font and stays that
// way. CSS-declared faces load lazily on first DOM use, which a canvas never
// triggers, so load it explicitly. Bounded: a missing font must never block the
// map, so after timeoutMs the map draws with the fallback.
export async function ensureCanvasFont(timeoutMs = 1500): Promise<boolean> {
  if (typeof document === 'undefined' || !document.fonts) return false;
  const spec = `${CANVAS_LABEL_FONT.weight} ${CANVAS_LABEL_FONT.sizePx}px ${uiFont()}`;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<false>((resolve) => {
    timer = setTimeout(() => resolve(false), timeoutMs);
  });
  try {
    return await Promise.race([
      document.fonts.load(spec).then((faces) => faces.length > 0),
      timeout,
    ]);
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

export function uiFont(): string {
  if (typeof document === 'undefined') return FALLBACK_UI_FONT;
  const value = getComputedStyle(document.documentElement).getPropertyValue('--font-ui').trim();
  return value || FALLBACK_UI_FONT;
}
