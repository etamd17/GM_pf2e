import { TERRAIN_KINDS, type TemplateShape } from '../gmCommands';
export type Tool = 'select' | 'token' | 'light' | 'wall' | 'fog-reveal' | 'fog-hide' | 'template' | 'terrain' | 'ping' | 'ruler';

const TOOLS: ReadonlyArray<{ id: Tool; label: string; hint: string }> = [
  { id: 'select', label: 'Select', hint: 'Select and drag tokens and templates; arrows move the selection' },
  { id: 'token', label: 'Token', hint: 'Click to place a token' },
  { id: 'light', label: 'Light', hint: 'Click to place a light' },
  { id: 'wall', label: 'Walls', hint: 'Click to start a wall, click again to end it; Esc stops' },
  { id: 'fog-reveal', label: 'Reveal', hint: 'Click a room to reveal it on the table (walls and closed doors bound it)' },
  { id: 'fog-hide', label: 'Hide', hint: 'Click a room to cover it with fog again' },
  { id: 'template', label: 'Template', hint: 'Burst or emanation: click. Cone or line: click the origin, then click to aim' },
  { id: 'terrain', label: 'Terrain', hint: 'Click a room to paint it; Alt-click paints one square' },
  // beta-1; Codex's tool-group rail (U-2) moves these into a Measure group.
  { id: 'ruler', label: 'Ruler', hint: 'Drag to measure; the TV shows it too' },
  { id: 'ping', label: 'Ping', hint: 'Click to ping a spot; the TV shows it too' },
];


const TEMPLATE_SHAPES = [{id: 'burst', label: 'Burst'}, {id: 'emanation', label: 'Emanation'}, {id: 'cone', label: 'Cone'}, {id: 'line', label: 'Line'}] as const;

interface Props {
  railOpen: boolean; tool: Tool; inputEnabled: boolean;
  templateShape: TemplateShape; templateSize: number; terrainKind: number;
  onToggle(): void; onChoose(tool: Tool): void;
  setTemplateShape(shape: TemplateShape): void; setTemplateSize(size: number): void; setTerrainKind(kind: number): void;
}

export function ToolRail({railOpen, tool, inputEnabled, templateShape, templateSize, terrainKind, onToggle, onChoose, setTemplateShape, setTemplateSize, setTerrainKind}: Props) {
  return (<nav className="vtt-rail" aria-label="Map tools">
        <button type="button" className="vtt-rail-toggle" aria-expanded={railOpen} onClick={() => onToggle()}>
          {railOpen ? 'Hide tools' : 'Tools'}
        </button>
        {railOpen &&
          TOOLS.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`vtt-tool ${tool === t.id ? 'is-active' : ''}`}
              aria-pressed={tool === t.id}
              title={t.hint}
              disabled={t.id !== 'select' && !inputEnabled}
              onClick={() => onChoose(t.id)}
            >
              {t.label}
            </button>
          ))}
        {railOpen && tool === 'template' && (
          <div className="vtt-tool-options" role="group" aria-label="Template options">
            <label className="vtt-field">
              <span>Shape</span>
              <select
                value={templateShape}
                onChange={(e) => {
                  setTemplateShape(e.target.value as TemplateShape);
                }}
              >
                {TEMPLATE_SHAPES.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="vtt-field">
              <span>Size (squares)</span>
              <input
                type="number"
                min={1}
                max={60}
                value={templateSize}
                onChange={(e) => setTemplateSize(Math.max(1, Math.min(60, Math.round(Number(e.target.value) || 1))))}
              />
            </label>
          </div>
        )}
        {railOpen && tool === 'terrain' && (
          <div className="vtt-tool-options" role="group" aria-label="Terrain options">
            <label className="vtt-field">
              <span>Paint</span>
              <select value={terrainKind} onChange={(e) => setTerrainKind(Number(e.target.value))}>
                {TERRAIN_KINDS.map((option) => (
                  <option key={option.kind} value={option.kind}>
                    {option.label}
                  </option>
                ))}
                <option value={0}>Erase</option>
              </select>
            </label>
          </div>
        )}
      </nav>);
}
