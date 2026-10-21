import { TERRAIN_KINDS, type TemplateShape } from '../gmCommands';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
export type Tool = 'select' | 'token' | 'light' | 'wall' | 'door' | 'fog-reveal' | 'fog-hide' | 'template' | 'terrain' | 'ping' | 'ruler';
type Group = 'select' | 'place' | 'measure' | 'templates' | 'walls' | 'fog' | 'terrain';
const GROUPS: ReadonlyArray<{id: Group; label: string; first: Tool; path: string}> = [
  {id:'select', label:'Select', first:'select', path:'M5 3 L19 13 L12 14 L9 21 Z'},
  {id:'place', label:'Place', first:'token', path:'M12 4 V20 M4 12 H20 M8 4 H4 V8 M16 20 H20 V16'},
  {id:'measure', label:'Measure', first:'ruler', path:'M3 8 H21 V16 H3 Z M7 8 V12 M12 8 V14 M17 8 V12'},
  {id:'templates', label:'Templates', first:'template', path:'M12 3 L21 20 H3 Z M12 10 V16'},
  {id:'walls', label:'Walls', first:'wall', path:'M3 4 H21 V20 H3 Z M3 12 H21 M9 4 V12 M15 12 V20'},
  {id:'fog', label:'Fog', first:'fog-reveal', path:'M3 8 H21 M6 12 H18 M3 16 H21 M8 20 H16 M8 4 H16'},
  {id:'terrain', label:'Terrain', first:'terrain', path:'M3 20 L9 5 L14 14 L18 8 L22 20 Z'},
];
const GROUP_OF: Record<Tool,Group> = {select:'select',token:'place',light:'place',ruler:'measure',ping:'measure',template:'templates',wall:'walls',door:'walls','fog-reveal':'fog','fog-hide':'fog',terrain:'terrain'};
const SUBTOOLS: Partial<Record<Group, ReadonlyArray<{id: Tool; label: string; hint: string}>>> = {
  place:[{id:'token',label:'Token',hint:'Click to place a token'}, {id:'light',label:'Light',hint:'Click to place a light'}],
  measure:[{id:'ruler',label:'Ruler',hint:'Drag to measure; the TV shows it too'}, {id:'ping',label:'Ping',hint:'Click to ping a spot on the TV'}],
  walls:[{id:'wall',label:'Wall',hint:'Click successive wall points; Escape stops'}, {id:'door',label:'Door',hint:'Click the two ends of a closed door; Escape stops'}],
  fog:[{id:'fog-reveal',label:'Reveal',hint:'Click a room to reveal it'}, {id:'fog-hide',label:'Hide',hint:'Click a room to cover it with fog'}],
};
const TEMPLATE_SHAPES = [{id:'burst',label:'Burst'},{id:'emanation',label:'Emanation'},{id:'cone',label:'Cone'},{id:'line',label:'Line'}] as const;
interface Props {
  railOpen: boolean; tool: Tool; inputEnabled: boolean;
  templateShape: TemplateShape; templateSize: number; terrainKind: number;
  onToggle(): void; onChoose(tool: Tool): void;
  setTemplateShape(shape: TemplateShape): void; setTemplateSize(size: number): void; setTerrainKind(kind: number): void;
  canResetFog?: boolean; onResetFog?(): void;
}
// The rail only selects tools/options. Commands and confirmation stay in the workspace.
export function ToolRail(p: Props) {
  const group = GROUP_OF[p.tool];
  const lastPaintKind = useRef(p.terrainKind > 0 ? p.terrainKind : 1);
  useEffect(() => { if (p.terrainKind > 0) lastPaintKind.current = p.terrainKind; }, [p.terrainKind]);
  const [hint, setHint] = useState<{id:string; text:string; top:number; left:number} | null>(null);
  function showHint(target: EventTarget | null) {
    const button = target instanceof Element ? target.closest('button') : null;
    const id = button?.getAttribute('aria-describedby');
    const text = id ? document.getElementById(id)?.textContent : null;
    if (!button || !id || !text) { setHint(null); return; }
    const rect = button.getBoundingClientRect();
    setHint(previous => previous?.id === id ? previous : {
      id, text, top:Math.max(8,Math.min(rect.top,window.innerHeight - 120)),
      left:Math.max(8,Math.min(rect.right + 8,window.innerWidth - 228)),
    });
  }
  const choose = (tool: Tool) => { if (tool === 'select' || p.inputEnabled) p.onChoose(tool); };
  return <nav className="vtt-rail" aria-label="Map tools" onMouseOver={e => showHint(e.target)}
    onMouseLeave={() => setHint(null)} onFocusCapture={e => showHint(e.target)} onBlurCapture={() => setHint(null)}
    onScroll={() => setHint(null)} onKeyDown={e => {if (e.key === 'Escape') setHint(null);}}>
    {hint && p.railOpen && createPortal(<div className="vtt-floating-tool-hint" aria-hidden="true"
      style={{top:hint.top,left:hint.left}}>{hint.text}</div>,document.body)}
    <button type="button" className="vtt-rail-toggle" aria-expanded={p.railOpen} onClick={p.onToggle}>{p.railOpen ? 'Hide tools' : 'Tools'}</button>
    {p.railOpen && GROUPS.map(g => <div className="vtt-tool-group" key={g.id}>
      <button type="button" className={`vtt-tool vtt-tool-group-button ${group === g.id ? 'is-active' : ''}`}
        aria-label={g.label} aria-pressed={group === g.id} disabled={g.id !== 'select' && !p.inputEnabled}
        aria-describedby={`tool-hint-${g.id}`} onClick={() => { if (group !== g.id || g.id === 'select') choose(g.first); }}>
        <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d={g.path} /></svg>
        <span>{g.label}</span>
        <span className="vtt-tool-hint" role="tooltip" id={`tool-hint-${g.id}`}>{g.label}: Enter or Space when focused</span>
      </button>
      {group === g.id && <div className="vtt-subtools" role="group" aria-label={`${g.label} tools`}>
        {SUBTOOLS[g.id]?.map(t => <button type="button" key={t.id} className={`vtt-subtool ${p.tool === t.id ? 'is-active' : ''}`}
          aria-label={t.label} aria-describedby={`tool-hint-${t.id}`} aria-pressed={p.tool === t.id} disabled={!p.inputEnabled} onClick={() => choose(t.id)}>
          {t.label}<span className="vtt-tool-hint" role="tooltip" id={`tool-hint-${t.id}`}>{t.hint}. Enter or Space when focused</span></button>)}
        {g.id === 'fog' && <button type="button" className="vtt-subtool" disabled={!p.inputEnabled || !p.canResetFog}
          aria-label="Reset fog" aria-describedby="tool-hint-reset" onClick={p.onResetFog}>Reset fog
          <span className="vtt-tool-hint" role="tooltip" id="tool-hint-reset">Cover the whole scene again; Enter or Space opens confirmation</span></button>}
        {g.id === 'templates' && <div className="vtt-tool-options" role="group" aria-label="Template options">
          <label className="vtt-field"><span>Shape</span><select aria-label="Shape" disabled={!p.inputEnabled} value={p.templateShape}
            onChange={e => p.setTemplateShape(e.target.value as TemplateShape)}>{TEMPLATE_SHAPES.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}</select></label>
          <label className="vtt-field"><span>Size (squares)</span><input disabled={!p.inputEnabled} type="number" min={1} max={60} value={p.templateSize}
            onChange={e => p.setTemplateSize(Math.max(1,Math.min(60,Math.round(Number(e.target.value)||1))))} /></label>
        </div>}
        {g.id === 'terrain' && <>
          <button type="button" className={`vtt-subtool ${p.terrainKind !== 0 ? 'is-active' : ''}`} aria-pressed={p.terrainKind !== 0}
            aria-label="Paint terrain" aria-describedby="tool-hint-paint" disabled={!p.inputEnabled} onClick={() => p.setTerrainKind(lastPaintKind.current)}>Paint terrain
            <span className="vtt-tool-hint" role="tooltip" id="tool-hint-paint">Paint terrain. Enter or Space when focused</span></button>
          <button type="button" className={`vtt-subtool ${p.terrainKind === 0 ? 'is-active' : ''}`} aria-pressed={p.terrainKind === 0}
            aria-label="Erase terrain" aria-describedby="tool-hint-erase" disabled={!p.inputEnabled} onClick={() => p.setTerrainKind(0)}>Erase terrain
            <span className="vtt-tool-hint" role="tooltip" id="tool-hint-erase">Erase terrain. Enter or Space when focused</span></button>
          <label className="vtt-field"><span>Paint</span><select aria-label="Paint" disabled={!p.inputEnabled} value={p.terrainKind}
            onChange={e => p.setTerrainKind(Number(e.target.value))}>{TERRAIN_KINDS.map(t => <option key={t.kind} value={t.kind}>{t.label}</option>)}<option value={0}>Erase</option></select></label>
        </>}
      </div>}
    </div>)}
  </nav>;
}
