import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ConnectionBanner } from '../components/ConnectionBanner';
import { MapHost } from '../components/MapHost';
import type { GmLive } from '../live/gm';
import type { GmEntity } from '../protocol/frames';
import { snapToGrid, snapTokenCentre, type Hit, type Point } from '../scene/interaction';
import type { RuntimeInteraction } from '../scene/SceneRuntime';
import { useCommands } from '../state/commands';
import { acceptsDurableInput, isTerminal, useConnection } from '../state/connection';
import { entityOf, useEntities } from '../state/entities';
import { useOverlays } from '../state/overlays';
import { useProjection } from '../state/projection';
import {
  TOKEN_COLORS,
  applyOverlays,
  clearTable,
  createLight,
  createScene,
  createToken,
  createWall,
  deleteEntity,
  fogReset,
  fogReveal,
  fogUpdate,
  moveToken,
  TERRAIN_KINDS,
  templateCreate,
  templateUpdate,
  terrainPaint,
  terrainReset,
  type TemplateShape,
  type TerrainCell,
  newEntityId,
  setDoorState,
  showOnTable,
  updateLight,
  updateScene,
  updateToken,
  type DoorState,
  type Draft,
  type TokenOverlay,
} from './gmCommands';
import type { GmBoot } from '../live/boot';
import { ImportPanel, PairingPanel } from './GmPanels';
import { ImagePicker } from './ImagePicker';
import { NoticeBar } from './NoticeBar';
import { decodeFogBits } from '../protocol/semantics';
import { cellAt, chunk, floodRegion, regionCells, terrainKindAt, type FillWall } from '../scene/fogFill';
import { inverseFor, overlayFor, targetOf } from './undo';

interface Props {
  sceneName: string;
  // The live wiring when served by Flask; null on the Vite dev server, where
  // the tools are shown but have nothing to send to.
  live?: GmLive | null;
}

type Tool = 'select' | 'token' | 'light' | 'wall' | 'fog-reveal' | 'fog-hide' | 'template' | 'terrain';

const TOOLS: ReadonlyArray<{ id: Tool; label: string; hint: string }> = [
  { id: 'select', label: 'Select', hint: 'Select and drag tokens and templates; arrows move the selection' },
  { id: 'token', label: 'Token', hint: 'Click to place a token' },
  { id: 'light', label: 'Light', hint: 'Click to place a light' },
  { id: 'wall', label: 'Walls', hint: 'Click to start a wall, click again to end it; Esc stops' },
  { id: 'fog-reveal', label: 'Reveal', hint: 'Click a room to reveal it on the table (walls and closed doors bound it)' },
  { id: 'fog-hide', label: 'Hide', hint: 'Click a room to cover it with fog again' },
  { id: 'template', label: 'Template', hint: 'Burst or emanation: click. Cone or line: click the origin, then click to aim' },
  { id: 'terrain', label: 'Terrain', hint: 'Click a room to paint it; Alt-click paints one square' },
];

// A whole template moved, aim and all, kept inside the contract's range.
function shiftTemplate(t: Of<'template'>, dx: number, dy: number) {
  const keep = (v: number) => Math.max(0, Math.min(16384, v));
  return { x: keep(t.x + dx), y: keep(t.y + dy), aimX: keep(t.aimX + dx), aimY: keep(t.aimY + dy) };
}

// The inspector's template colours: /map's one colour first, then the token set.
const TEMPLATE_COLORS = [0x75c5eb, ...TOKEN_COLORS] as const;

const TEMPLATE_SHAPES: ReadonlyArray<{ id: TemplateShape; label: string }> = [
  { id: 'burst', label: 'Burst' },
  { id: 'emanation', label: 'Emanation' },
  { id: 'cone', label: 'Cone' },
  { id: 'line', label: 'Line' },
];

type Of<K extends GmEntity['kind']> = Extract<GmEntity, { kind: K }>;

export interface SceneSummary {
  id: string;
  name: string;
  published: boolean;
  onTable: boolean;
}

// Map-first command deck: compact status bar, tool rail, map in the centre,
// inspector dock. Every edit is a command issued through live.issue with the
// edited entity's current version (gmCommands.ts); nothing changes locally
// except a token's optimistic overlay until its frame arrives.
export function GmWorkspace({ sceneName, live = null }: Props) {
  const status = useConnection((s) => s.status);
  const renderer = useConnection((s) => s.renderer);
  const projected = useProjection((s) => s.scene);
  const entities = useEntities((s) => s.entities);
  const overlays = useOverlays((s) => s.overlays);
  const pending = useCommands((s) => s.pending);
  const lastApplied = useCommands((s) => s.lastApplied);
  const [zoom, setZoom] = useState(1);
  const [railOpen, setRailOpen] = useState(true);
  const [dockOpen, setDockOpen] = useState(true);
  const [tool, setTool] = useState<Tool>('select');
  const [selection, setSelection] = useState<Hit | null>(null);
  const [wallStart, setWallStart] = useState<Point | null>(null);
  const [wallEnd, setWallEnd] = useState<Point | null>(null);
  // Templates and terrain (parity P2): the tool options, and a cone or line
  // waiting for its aim.
  const [templateShape, setTemplateShape] = useState<TemplateShape>('burst');
  const [templateSize, setTemplateSize] = useState(3);
  const [templateStart, setTemplateStart] = useState<Point | null>(null);
  const [terrainKind, setTerrainKind] = useState(1);
  const [announcement, setAnnouncement] = useState('');
  const [scenes, setScenes] = useState<SceneSummary[] | null>(null);
  const [creatingScene, setCreatingScene] = useState<{ commandId: string; sceneId: string } | null>(null);

  const inputEnabled = acceptsDurableInput(status) && renderer === 'ok' && live !== null;
  const showMap = projected !== null && !isTerminal(status);
  const scene = useMemo(
    () => (projected ? applyOverlays(projected, overlays.values() as Iterable<TokenOverlay | null>) : null),
    [projected, overlays],
  );
  const sceneEntity = useMemo(() => [...entities.values()].find((e): e is Of<'scene'> => e.kind === 'scene'), [entities]);
  const fogEntity = useMemo(() => [...entities.values()].find((e): e is Of<'fog'> => e.kind === 'fog'), [entities]);
  const terrainEntity = useMemo(
    () => [...entities.values()].find((e): e is Of<'terrain'> => e.kind === 'terrain'),
    [entities],
  );
  const selected = selection ? (entityOf(selection.kind, selection.id) ?? null) : null;

  // A selection whose entity is gone (deleted here or elsewhere) is dropped.
  useEffect(() => {
    if (selection && !entityOf(selection.kind, selection.id)) setSelection(null);
  }, [entities, selection]);

  const tableCommands = useRef(new Set<string>());
  // Issue a command, with what would reverse it (read from the entity as it is
  // just before), for the edit history (undo.ts).
  const send = useCallback(
    (draft: Draft, overlay: TokenOverlay | null) => {
      if (!live) return { commandId: null, inverse: null };
      const target = targetOf(draft);
      const before = target ? entityOf(target.kind, target.id) : undefined;
      const commandId = live.issue(draft, overlay);
      if (commandId && (draft.type === 'table.show' || draft.type === 'table.clear')) tableCommands.current.add(commandId);
      return { commandId, inverse: commandId ? inverseFor(draft, before) : null };
    },
    [live],
  );
  // Every edit goes through here. Issuing one makes redo obsolete at once.
  const issue = useCallback(
    (draft: Draft, overlay: TokenOverlay | null = null) => {
      const { commandId, inverse } = send(draft, overlay);
      live?.undo.edit(commandId, inverse);
      return commandId;
    },
    [live, send],
  );

  const step = useCallback(
    (direction: 'undo' | 'redo') => {
      if (!live || !inputEnabled) return;
      // Nothing starts while a command of this page is still on its way.
      const entry = live.undo.beginStep(direction);
      if (!entry) return;
      const draft = entry.make(entityOf(entry.kind, entry.id));
      if (!draft) {
        useCommands.getState().notify({
          kind: 'info',
          text: `Cannot ${direction} the last ${entry.label}: it was changed or removed since.`,
        });
        return;
      }
      const { commandId, inverse } = send(draft, overlayFor(draft));
      live.undo.stepIssued(direction, entry, commandId, inverse);
    },
    [live, inputEnabled, send],
  );
  const undoState = useCommands((s) => s.undo);

  // Ctrl+Z / Cmd+Z undo, with Shift (or Ctrl+Y) to redo. Not while typing in a
  // field: there the browser's own text undo applies.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT')) return;
      const key = e.key.toLowerCase();
      if (key === 'z') {
        e.preventDefault();
        step(e.shiftKey ? 'redo' : 'undo');
      } else if (key === 'y') {
        e.preventDefault();
        step('redo');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [step]);

  // --- scenes -------------------------------------------------------------------------------
  const refreshScenes = useCallback(async () => {
    if (!live) return;
    try {
      const response = await fetch(`/vtt/api/scenes?campaignId=${encodeURIComponent(live.boot.campaignId)}`, {
        credentials: 'same-origin',
      });
      if (response.ok) setScenes(((await response.json()) as { scenes: SceneSummary[] }).scenes);
    } catch {
      /* the picker keeps its last list */
    }
  }, [live]);

  useEffect(() => {
    void refreshScenes();
  }, [refreshScenes, sceneEntity?.version]);

  // Showing or clearing the table moves the table pointer without changing
  // this scene, so the picker's "(on the table)" would go stale: refetch once
  // such a command is applied. Subscribed directly so no outcome is missed
  // between renders.
  useEffect(
    () =>
      useCommands.subscribe((state, previous) => {
        const id = state.lastApplied;
        if (id && id !== previous.lastApplied && tableCommands.current.delete(id)) void refreshScenes();
      }),
    [refreshScenes],
  );

  const openScene = useCallback(
    (sceneId: string) => {
      if (!live) return;
      // Opening a scene reloads the page; a change still being saved would be
      // lost with it.
      if (
        useCommands.getState().pending.length > 0 &&
        !window.confirm('A change is still being saved. Leave this scene anyway?')
      ) {
        return;
      }
      const url = new URL(window.location.href);
      url.searchParams.set('sceneId', sceneId);
      window.location.assign(url.toString());
    },
    [live],
  );

  // A created scene is opened once its command has applied.
  useEffect(() => {
    if (creatingScene && lastApplied === creatingScene.commandId) openScene(creatingScene.sceneId);
  }, [creatingScene, lastApplied, openScene]);

  function newScene() {
    const name = window.prompt('Name the new scene', 'New scene')?.trim();
    if (!name) return;
    const sceneId = newEntityId('scene');
    const commandId = issue(createScene(sceneId, name.slice(0, 120)));
    if (commandId) setCreatingScene({ commandId, sceneId });
  }

  // --- fog (parity P1) --------------------------------------------------------------------------
  // One click reveals (or hides) the room under it: a flood bounded by walls
  // and closed doors, sent as only the cells that change (fogFill.ts).
  const revealRoom = (point: Point, mode: 'reveal' | 'hide') => {
    if (!scene) return;
    const notify = (text: string) => useCommands.getState().notify({ kind: 'info', text });
    if (!fogEntity) {
      notify('This scene is too large for fog.');
      return;
    }
    const bits = decodeFogBits(fogEntity.revealed);
    if (!bits) return;
    const grid = { cols: fogEntity.cols, rows: fogEntity.rows, gridSize: scene.gridSize, bits };
    const start = cellAt(grid, point.x, point.y);
    if (!start) return;
    const walls = [...entities.values()].filter((e): e is Of<'wall'> => e.kind === 'wall') as FillWall[];
    const cells = floodRegion(grid, start, walls, mode);
    if (cells.length === 0) {
      notify(mode === 'reveal' ? 'That area is already revealed.' : 'That area is already hidden.');
      return;
    }
    for (const part of chunk(cells)) issue(fogReveal(mode, part));
    if (!fogEntity.enabled && mode === 'reveal') notify('Fog is off, so the table already shows everything. Turn it on in the Scene panel.');
  };

  // --- templates and terrain (parity P2) -------------------------------------------------------
  const placeTemplate = (point: Point, hit: Hit | null) => {
    if (!scene) return;
    if (templateShape === 'burst') {
      const at = snapToGrid(point, scene);
      issue(templateCreate(newEntityId('tpl'), 'burst', at, at, templateSize));
      return;
    }
    if (templateShape === 'emanation') {
      // Started on a token: centred on it and grown by its footprint, as on
      // /map, but not linked to it afterwards.
      const token = hit?.kind === 'token' ? (entityOf('token', hit.id) as Of<'token'> | undefined) : undefined;
      const at = token ? { x: token.x, y: token.y } : snapToGrid(point, scene);
      const size = Math.min(60, token ? Math.ceil(templateSize + token.size / 2) : templateSize);
      issue(templateCreate(newEntityId('tpl'), 'emanation', at, at, size));
      return;
    }
    // Cone or line: the first click sets the origin, the second aims.
    if (!templateStart) {
      setTemplateStart(snapToGrid(point, scene));
      return;
    }
    if (point.x !== templateStart.x || point.y !== templateStart.y) {
      issue(templateCreate(newEntityId('tpl'), templateShape, templateStart, point, templateSize));
    }
    setTemplateStart(null);
    setWallEnd(null);
  };

  const paintTerrain = (point: Point, single: boolean) => {
    if (!scene) return;
    const notify = (text: string) => useCommands.getState().notify({ kind: 'info', text });
    if (!terrainEntity) {
      notify('This scene is too large for terrain.');
      return;
    }
    const cells = decodeFogBits(terrainEntity.cells);
    if (!cells) return;
    const grid = { cols: terrainEntity.cols, rows: terrainEntity.rows, gridSize: scene.gridSize };
    const start = cellAt(grid, point.x, point.y);
    if (!start) return;
    const walls = [...entities.values()].filter((e): e is Of<'wall'> => e.kind === 'wall') as FillWall[];
    const region = single ? [start] : regionCells(grid, start, walls);
    const changed: TerrainCell[] = region
      .filter(([c, r]) => terrainKindAt(cells, grid.cols, c, r) !== terrainKind)
      .map(([c, r]) => [c, r, terrainKind]);
    if (changed.length === 0) {
      notify('That area already looks like that.');
      return;
    }
    for (const part of chunk(changed)) issue(terrainPaint(part));
  };

  // --- map input ------------------------------------------------------------------------------
  const tokenCount = scene?.tokens.length ?? 0;
  const interaction: RuntimeInteraction = {
    canDragToken: (id) => inputEnabled && tool === 'select' && !(entityOf('token', id) as Of<'token'> | undefined)?.locked,
    onTokenDrop: (id, x, y) => {
      const token = entityOf('token', id) as Of<'token'> | undefined;
      if (!token || !inputEnabled) return;
      if (token.x === x && token.y === y) return;
      issue(moveToken(token, x, y), { kind: 'token', id, patch: { x, y } });
      setSelection({ kind: 'token', id });
    },
    canDragTemplate: (id) => inputEnabled && tool === 'select' && entityOf('template', id) !== undefined,
    onTemplateDrop: (id, dx, dy) => {
      const template = entityOf('template', id) as Of<'template'> | undefined;
      if (!template || !inputEnabled) return;
      issue(templateUpdate(template, shiftTemplate(template, dx, dy)));
      setSelection({ kind: 'template', id });
    },
    onClick: (point, hit, modifiers) => {
      if (!scene) return;
      if (tool === 'select' || !inputEnabled) {
        setSelection(hit);
        if (hit) announce(hit);
        return;
      }
      if (tool === 'token') {
        const id = newEntityId('tok');
        const at = snapTokenCentre(point, 1, scene);
        const label = `Token ${tokenCount + 1}`;
        const color = TOKEN_COLORS[tokenCount % TOKEN_COLORS.length] ?? 0xc9a34e;
        issue(createToken(id, at, label, color), {
          kind: 'token',
          id,
          create: { id, x: at.x, y: at.y, size: 1, label, color },
        });
      } else if (tool === 'light') {
        issue(createLight(newEntityId('light'), snapToGrid(point, scene), scene.gridSize));
      } else if (tool === 'fog-reveal' || tool === 'fog-hide') {
        revealRoom(point, tool === 'fog-reveal' ? 'reveal' : 'hide');
      } else if (tool === 'template') {
        placeTemplate(point, hit);
      } else if (tool === 'terrain') {
        paintTerrain(point, modifiers?.alt === true);
      } else if (tool === 'wall') {
        const p = snapToGrid(point, scene);
        if (!wallStart) {
          setWallStart(p);
          setWallEnd(p);
        } else if (p.x !== wallStart.x || p.y !== wallStart.y) {
          issue(createWall(newEntityId('wall'), wallStart, p));
          setWallStart(p); // keep drawing from here
        }
      }
    },
    onPointerMove: (point) => {
      if (tool === 'wall' && wallStart && scene) setWallEnd(snapToGrid(point, scene));
      if (tool === 'template' && templateStart) setWallEnd(point); // the aim preview line
    },
    onKey: (e) => {
      if (e.key === 'Escape') {
        setWallStart(null);
        setWallEnd(null);
        setTemplateStart(null);
        setSelection(null);
        return true;
      }
      if (!inputEnabled || !selected || !scene) return false;
      // Delete only: Backspace is too easy to hit by accident on a map.
      if (e.key === 'Delete' && selected.kind !== 'scene' && selected.kind !== 'fog' && selected.kind !== 'terrain') {
        issue(deleteEntity(selected), selected.kind === 'token' ? { kind: 'token', id: selected.id, deleted: true } : null);
        setSelection(null);
        return true;
      }
      if (selected.kind === 'template' && e.key.startsWith('Arrow')) {
        // Move the whole template one square, aim and all.
        const g = scene.gridSize;
        const dx = e.key === 'ArrowLeft' ? -g : e.key === 'ArrowRight' ? g : 0;
        const dy = e.key === 'ArrowUp' ? -g : e.key === 'ArrowDown' ? g : 0;
        issue(templateUpdate(selected, shiftTemplate(selected, dx, dy)));
        return true;
      }
      if (selected.kind === 'token' && !selected.locked && e.key.startsWith('Arrow')) {
        const g = scene.gridSize;
        const dx = e.key === 'ArrowLeft' ? -g : e.key === 'ArrowRight' ? g : 0;
        const dy = e.key === 'ArrowUp' ? -g : e.key === 'ArrowDown' ? g : 0;
        const at = snapTokenCentre({ x: selected.x + dx, y: selected.y + dy }, selected.size, scene);
        if (at.x !== selected.x || at.y !== selected.y) {
          issue(moveToken(selected, at.x, at.y), { kind: 'token', id: selected.id, patch: at });
        }
        return true;
      }
      return false;
    },
  };

  function announce(hit: Hit) {
    const e = entityOf(hit.kind, hit.id);
    if (!e) return;
    const name = e.kind === 'token' ? e.label || 'Unnamed token' : e.kind === 'light' ? 'Light' : e.kind === 'wall' ? (e.doorState === 'none' ? 'Wall' : 'Door') : 'Scene';
    setAnnouncement(`${name} selected`);
  }

  function chooseTool(next: Tool) {
    setTool(next);
    setWallStart(null);
    setWallEnd(null);
    setTemplateStart(null);
  }

  const visibleTokens = scene?.tokens ?? [];

  return (
    <div className={`vtt-gm ${railOpen ? '' : 'is-rail-collapsed'} ${dockOpen ? '' : 'is-dock-collapsed'}`}>
      <header className="vtt-topbar">
        <span className="vtt-brand">VTT</span>
        {live && scenes ? (
          <select
            className="vtt-scene-picker"
            aria-label="Scene"
            value={sceneEntity?.id ?? ''}
            onChange={(e) => e.target.value && openScene(e.target.value)}
          >
            {!sceneEntity && <option value="">No scene</option>}
            {scenes.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
                {s.onTable ? ' (on the table)' : s.published ? '' : ' (prep)'}
              </option>
            ))}
          </select>
        ) : (
          <span className="vtt-scene-name">{sceneEntity?.name ?? sceneName}</span>
        )}
        <button type="button" className="vtt-topbar-btn" onClick={newScene} disabled={!inputEnabled}>
          New scene
        </button>
        {sceneEntity && (
          <>
            <button
              type="button"
              className="vtt-topbar-btn"
              disabled={!inputEnabled}
              onClick={() => issue(updateScene(sceneEntity, { published: !sceneEntity.published }))}
            >
              {sceneEntity.published ? 'Unpublish' : 'Publish'}
            </button>
            <button type="button" className="vtt-topbar-btn" disabled={!inputEnabled} onClick={() => issue(showOnTable(sceneEntity))}>
              Show on table
            </button>
            <button type="button" className="vtt-topbar-btn" disabled={!inputEnabled} onClick={() => issue(clearTable(sceneEntity))}>
              Clear table
            </button>
          </>
        )}
        <button
          type="button"
          className="vtt-topbar-btn"
          disabled={!inputEnabled || !undoState.canUndo}
          title={undoState.next ? `Undo ${undoState.next} (Ctrl+Z)` : 'Undo (Ctrl+Z)'}
          onClick={() => step('undo')}
        >
          Undo
        </button>
        <button
          type="button"
          className="vtt-topbar-btn"
          disabled={!inputEnabled || !undoState.canRedo}
          title="Redo (Ctrl+Shift+Z)"
          onClick={() => step('redo')}
        >
          Redo
        </button>
        <span className="vtt-topbar-spacer" />
        {pending.length > 0 && (
          <span className="vtt-pending" aria-live="polite">
            {pending.some((p) => p.state !== 'sent') ? 'Waiting to send' : 'Saving'} ({pending.length})
          </span>
        )}
        <span className="vtt-zoom" aria-label="Zoom level">
          {Math.round(zoom * 100)}%
        </span>
        <span className={`vtt-conn vtt-conn--${status}`}>{status}</span>
      </header>

      <ConnectionBanner status={status} renderer={renderer} variant="gm" />

      <nav className="vtt-rail" aria-label="Map tools">
        <button type="button" className="vtt-rail-toggle" aria-expanded={railOpen} onClick={() => setRailOpen((v) => !v)}>
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
              onClick={() => chooseTool(t.id)}
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
                  setTemplateStart(null);
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
      </nav>

      <main className="vtt-stage">
        <NoticeBar live={live} />
        {showMap && scene && (
          <MapHost
            surface="gm"
            scene={scene}
            label={`Map: ${sceneEntity?.name ?? sceneName}`}
            onViewportChange={(v) => setZoom(v.scale)}
            interaction={interaction}
            selection={selection}
            wallDraft={
              tool === 'wall' && wallStart && wallEnd
                ? { a: wallStart, b: wallEnd }
                : tool === 'template' && templateStart && wallEnd
                  ? { a: templateStart, b: wallEnd }
                  : null
            }
          />
        )}
        {/* The canvas has an off-screen mirror for screen readers. */}
        <ul className="vtt-visually-hidden" aria-label="Tokens on the map">
          {visibleTokens.map((t) => (
            <li key={t.id}>
              <button type="button" onClick={() => setSelection({ kind: 'token', id: t.id })}>
                {t.label || 'Unnamed token'}
                {t.hidden ? ' (hidden from the table)' : ''}
              </button>
            </li>
          ))}
        </ul>
        <p className="vtt-visually-hidden" role="status" aria-live="polite">
          {announcement}
        </p>
      </main>

      <aside className="vtt-dock" aria-label="Inspector">
        <button type="button" className="vtt-dock-toggle" aria-expanded={dockOpen} onClick={() => setDockOpen((v) => !v)}>
          {dockOpen ? 'Hide inspector' : 'Inspector'}
        </button>
        {dockOpen && (
          <Inspector
            entity={selected}
            enabled={inputEnabled}
            onIssue={issue}
            onDeleted={() => setSelection(null)}
            scene={sceneEntity ?? null}
            fog={fogEntity ?? null}
            terrain={terrainEntity ?? null}
            {...(live ? { boot: live.boot } : {})}
          />
        )}
        {dockOpen && live && (
          <>
            <PairingPanel boot={live.boot} />
            <ImportPanel boot={live.boot} onImported={() => void refreshScenes()} />
          </>
        )}
      </aside>

      <footer className="vtt-tray" aria-label="Initiative and chat">
        <span className="vtt-empty">No encounter running.</span>
      </footer>
    </div>
  );
}

const DOOR_STATES: readonly DoorState[] = ['none', 'closed', 'open', 'locked', 'secret'];
const DOOR_LABEL: Record<DoorState, string> = {
  none: 'Wall',
  closed: 'Door (closed)',
  open: 'Door (open)',
  locked: 'Door (locked)',
  secret: 'Secret door',
};

interface InspectorProps {
  entity: GmEntity | null;
  enabled: boolean;
  onIssue(draft: Draft, overlay?: TokenOverlay | null): string | null;
  onDeleted(): void;
  // Images (M6): the open scene, for its background when nothing is selected,
  // and how this page uploads and previews assets.
  scene?: Extract<GmEntity, { kind: 'scene' }> | null;
  boot?: Pick<GmBoot, 'campaignId' | 'csrf'>;
  // Fog (parity P1): the scene's fog entity, absent when the grid is too large.
  fog?: Extract<GmEntity, { kind: 'fog' }> | null;
  // Terrain (parity P2), likewise.
  terrain?: Extract<GmEntity, { kind: 'terrain' }> | null;
}

const mediaUrl = (boot: Pick<GmBoot, 'campaignId'>, id: string | null) =>
  id ? `/vtt/media/${encodeURIComponent(boot.campaignId)}/${encodeURIComponent(id)}` : null;

// The selected entity's fields. A text field commits on blur or Enter, never
// on every keystroke; a field the GM is editing is not overwritten by a frame
// until they leave it (the key resets the field when the entity's version
// changes while it is not focused).
export function Inspector({ entity, enabled, onIssue, onDeleted, scene, boot, fog, terrain }: InspectorProps) {
  if (!entity || entity.kind === 'scene' || entity.kind === 'fog' || entity.kind === 'terrain') {
    return (
      <div className="vtt-inspector">
        {scene && boot && (
          <>
            <h2 className="vtt-inspector-title">Scene</h2>
            <ImagePicker
              label="Background"
              boot={boot}
              current={scene.backgroundAssetId}
              previewUrl={mediaUrl(boot, scene.backgroundAssetId)}
              disabled={!enabled}
              onChange={(backgroundAssetId) => onIssue(updateScene(scene, { backgroundAssetId }))}
            />
          </>
        )}
        {fog ? (
          <>
            <Toggle label="Fog of war" checked={fog.enabled} disabled={!enabled} onChange={(on) => onIssue(fogUpdate(fog, on))} />
            <button
              type="button"
              className="vtt-danger"
              disabled={!enabled}
              onClick={() => window.confirm('Cover the whole scene with fog again?') && onIssue(fogReset(fog))}
            >
              Reset fog
            </button>
          </>
        ) : (
          scene && <p className="vtt-empty">This scene is too large for fog.</p>
        )}
        {terrain && terrain.cells.replace(/A/g, '') !== '' && (
          <button
            type="button"
            className="vtt-danger"
            disabled={!enabled}
            onClick={() => window.confirm('Erase all painted terrain in this scene?') && onIssue(terrainReset(terrain))}
          >
            Reset terrain
          </button>
        )}
        <p className="vtt-empty">Select a token, light or wall to inspect it.</p>
      </div>
    );
  }
  const remove = () => {
    onIssue(deleteEntity(entity), entity.kind === 'token' ? { kind: 'token', id: entity.id, deleted: true } : null);
    onDeleted();
  };
  return (
    <div className="vtt-inspector" key={`${entity.kind}:${entity.id}`}>
      {entity.kind === 'token' && (
        <>
          <h2 className="vtt-inspector-title">Token</h2>
          <TextField
            label="Label"
            value={entity.label}
            version={entity.version}
            maxLength={80}
            disabled={!enabled}
            onCommit={(label) => label !== entity.label && onIssue(updateToken(entity, { label }), { kind: 'token', id: entity.id, patch: { label } })}
          />
          <label className="vtt-field">
            <span>Size</span>
            <select
              value={String(entity.size)}
              disabled={!enabled}
              onChange={(e) => {
                const size = Number(e.target.value);
                onIssue(updateToken(entity, { size }), { kind: 'token', id: entity.id, patch: { size } });
              }}
            >
              {[0.5, 1, 2, 3, 4].map((s) => (
                <option key={s} value={String(s)}>
                  {s === 0.5 ? 'Tiny' : s === 1 ? 'Medium' : s === 2 ? 'Large' : s === 3 ? 'Huge' : 'Gargantuan'}
                </option>
              ))}
              {![0.5, 1, 2, 3, 4].includes(entity.size) && <option value={String(entity.size)}>{entity.size} squares</option>}
            </select>
          </label>
          <Toggle
            label="Hidden from the table"
            checked={entity.hidden}
            disabled={!enabled}
            onChange={(hidden) => onIssue(updateToken(entity, { hidden }), { kind: 'token', id: entity.id, patch: { hidden } })}
          />
          <Toggle
            label="Locked"
            checked={entity.locked}
            disabled={!enabled}
            onChange={(locked) => onIssue(updateToken(entity, { locked }))}
          />
          {boot && (
            <ImagePicker
              label="Art"
              boot={boot}
              current={entity.artAssetId}
              previewUrl={mediaUrl(boot, entity.artAssetId)}
              disabled={!enabled}
              onChange={(artAssetId) => onIssue(updateToken(entity, { artAssetId }))}
            />
          )}
        </>
      )}
      {entity.kind === 'light' && (
        <>
          <h2 className="vtt-inspector-title">Light</h2>
          <TextField
            label="Radius (px)"
            value={String(entity.radius)}
            version={entity.version}
            maxLength={5}
            disabled={!enabled}
            onCommit={(text) => {
              const radius = Number(text);
              if (Number.isFinite(radius) && radius >= 0 && radius <= 16384 && radius !== entity.radius) {
                onIssue(updateLight(entity, { radius }));
              }
            }}
          />
          <Toggle label="Hidden from the table" checked={entity.hidden} disabled={!enabled} onChange={(hidden) => onIssue(updateLight(entity, { hidden }))} />
        </>
      )}
      {entity.kind === 'template' && (
        <>
          <h2 className="vtt-inspector-title">{TEMPLATE_SHAPES.find((t) => t.id === entity.shape)?.label ?? 'Template'}</h2>
          <TextField
            label="Size (squares)"
            value={String(entity.size)}
            version={entity.version}
            maxLength={2}
            disabled={!enabled}
            onCommit={(text) => {
              const size = Math.round(Number(text));
              if (Number.isFinite(size) && size >= 1 && size <= 60 && size !== entity.size) onIssue(templateUpdate(entity, { size }));
            }}
          />
          <Toggle
            label="Hidden from the table"
            checked={entity.hidden}
            disabled={!enabled}
            onChange={(hidden) => onIssue(templateUpdate(entity, { hidden }))}
          />
          <div className="vtt-field">
            <span id={`tpl-colour-${entity.id}`}>Colour</span>
            <div className="vtt-swatches" role="radiogroup" aria-labelledby={`tpl-colour-${entity.id}`}>
              {TEMPLATE_COLORS.map((color) => {
                const hex = `#${color.toString(16).padStart(6, '0')}`;
                return (
                  <button
                    key={color}
                    type="button"
                    role="radio"
                    aria-checked={entity.color === color}
                    aria-label={hex}
                    className="vtt-swatch"
                    style={{ background: hex }}
                    disabled={!enabled}
                    onClick={() => entity.color !== color && onIssue(templateUpdate(entity, { color }))}
                  />
                );
              })}
            </div>
          </div>
          <p className="vtt-empty">Drag it, or use the arrow keys, to move it a square at a time.</p>
        </>
      )}
      {entity.kind === 'wall' && (
        <>
          <h2 className="vtt-inspector-title">{DOOR_LABEL[entity.doorState]}</h2>
          <label className="vtt-field">
            <span>Kind</span>
            <select
              value={entity.doorState}
              disabled={!enabled}
              onChange={(e) => onIssue(setDoorState(entity, e.target.value as DoorState))}
            >
              {DOOR_STATES.map((d) => (
                <option key={d} value={d}>
                  {DOOR_LABEL[d]}
                </option>
              ))}
            </select>
          </label>
        </>
      )}
      <button type="button" className="vtt-danger" disabled={!enabled} onClick={remove}>
        Delete
      </button>
    </div>
  );
}

function TextField(props: {
  label: string;
  value: string;
  version: string;
  maxLength: number;
  disabled: boolean;
  onCommit(value: string): void;
}) {
  const [draft, setDraft] = useState(props.value);
  const [editing, setEditing] = useState(false);
  // A newer version from the server replaces the field, unless the GM is
  // typing in it right now (the legacy rule: never repaint a field mid-edit).
  useEffect(() => {
    if (!editing) setDraft(props.value);
  }, [props.value, props.version, editing]);
  const commit = () => {
    setEditing(false);
    const value = draft.trim();
    if (value !== props.value) props.onCommit(value);
  };
  return (
    <label className="vtt-field">
      <span>{props.label}</span>
      <input
        value={draft}
        maxLength={props.maxLength}
        disabled={props.disabled}
        onFocus={() => setEditing(true)}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
          if (e.key === 'Escape') {
            setDraft(props.value);
            setEditing(false);
          }
        }}
      />
    </label>
  );
}

function Toggle(props: { label: string; checked: boolean; disabled: boolean; onChange(value: boolean): void }) {
  return (
    <label className="vtt-field vtt-field--toggle">
      <input type="checkbox" checked={props.checked} disabled={props.disabled} onChange={(e) => props.onChange(e.target.checked)} />
      <span>{props.label}</span>
    </label>
  );
}
