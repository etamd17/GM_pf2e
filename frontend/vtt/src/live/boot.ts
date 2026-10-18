// The page's boot data. Flask injects it as
//   <script id="vtt-boot" type="application/json">{...}</script>
// (vtt/transport/pages.py). It is never executed, only parsed, and it carries
// nothing a page could not already ask the server for.

export interface GmBoot {
  campaignId: string;
  sceneId: string | null;
  csrf: string;
  // The signed-in member's stable id. Saved commands are bound to it.
  memberId: string;
  // The campaign's game system (beta-1): it decides how the ruler counts distance.
  system: 'pf2e' | 'cosmere' | 'dnd5e';
}

export function readBoot(doc: Document = document): Record<string, unknown> | null {
  const node = doc.getElementById('vtt-boot');
  if (!node?.textContent) return null;
  try {
    const value: unknown = JSON.parse(node.textContent);
    return value !== null && typeof value === 'object' ? (value as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export function gmBoot(raw: Record<string, unknown> | null): GmBoot | null {
  if (!raw) return null;
  const { campaignId, sceneId, csrf, memberId, system } = raw;
  if (typeof campaignId !== 'string' || typeof csrf !== 'string' || typeof memberId !== 'string') return null;
  if (sceneId !== null && typeof sceneId !== 'string') return null;
  const known = system === 'cosmere' || system === 'dnd5e' ? system : 'pf2e';
  return { campaignId, sceneId, csrf, memberId, system: known };
}
