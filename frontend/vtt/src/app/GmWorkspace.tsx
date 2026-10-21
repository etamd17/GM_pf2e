import { Inspector } from './Inspector';
export { Inspector } from './Inspector';
import { SceneBar } from './shell/SceneBar';
import { WorkspaceSidebar, useSidebarTab } from './shell/WorkspaceSidebar';
import { FirstRunGuidance } from './shell/FirstRunGuidance';
import { createViewportController } from './actions/viewport';
import { ViewportButtons } from './shell/ViewportButtons';
import { ToolRail, type Tool } from './shell/ToolRail';
import { EncounterLine } from './shell/EncounterLine';
import { WorkspaceStatus } from './shell/WorkspaceStatus';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ConnectionBanner } from '../components/ConnectionBanner';
import { MapHost } from '../components/MapHost';
import type { GmLive } from '../live/gm';
import type { GmEntity } from '../protocol/frames';
import { createRuler, ping, type RulerController } from './actions/transients';
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
  fogReveal,
  fogReset,
  moveToken,
  templateCreate,
  templateUpdate,
  terrainPaint,
  type TemplateShape,
  type TerrainCell,
  newEntityId,
  showOnTable,
  updateScene,
  type Draft,
  type TokenOverlay,
} from './gmCommands';
import { ImportPanel, PairingPanel } from './GmPanels';
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

// A whole template moved, aim and all, kept inside the contract's range.
function shiftTemplate(t: Of<'template'>, dx: number, dy: number) {
  const keep = (v: number) => Math.max(0, Math.min(16384, v));
  return { x: keep(t.x + dx), y: keep(t.y + dy), aimX: keep(t.aimX + dx), aimY: keep(t.aimY + dy) };
}

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
  // beta-1: one ruler controller per live session (throttle and linger rules).
  const rulerRef = useRef<RulerController | null>(null);
  useEffect(() => {
    if (!live) return;
    const ruler = createRuler(live.sendTransient, live.boot.system);
    rulerRef.current = ruler;
    return () => {
      ruler.dispose();
      if (rulerRef.current === ruler) rulerRef.current = null;
    };
  }, [live]);
  const lastApplied = useCommands((s) => s.lastApplied);
  const [zoom, setZoom] = useState(1);
  const viewport = useMemo(createViewportController, []);
  const [railOpen, setRailOpen] = useState(true);
  const [dockOpen, setDockOpen] = useState(true);
  const [sidebarTab, setSidebarTab] = useSidebarTab(live?.boot.memberId);

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
  // Tracker actors (parity P3): party characters first, then the encounter, by name.
  const actorEntities = useMemo(
    () =>
      [...entities.values()]
        .filter((e): e is Of<'actor'> => e.kind === 'actor')
        .sort((a, b) => Number(b.pc) - Number(a.pc) || a.name.localeCompare(b.name) || (a.id < b.id ? -1 : 1)),
    [entities],
  );
  // User selection opens Inspector, including reselecting the same token.
  // Mirror updates never steal the chosen tab.
  function selectEntity(hit: Hit | null) {
    setSelection(hit);
    if (hit) {
      setSidebarTab('inspector');
      setDockOpen(true);
    }
  }
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
      selectEntity({ kind: 'token', id });
    },
    canDragTemplate: (id) => inputEnabled && tool === 'select' && entityOf('template', id) !== undefined,
    measuring: () => tool === 'ruler' && live !== null,
    onMeasure: (points, done) => {
      const ruler = rulerRef.current;
      if (!ruler) return;
      if (done) ruler.end(points);
      else if (points) ruler.update(points);
    },
    onTemplateDrop: (id, dx, dy) => {
      const template = entityOf('template', id) as Of<'template'> | undefined;
      if (!template || !inputEnabled) return;
      issue(templateUpdate(template, shiftTemplate(template, dx, dy)));
      selectEntity({ kind: 'template', id });
    },
    onClick: (point, hit, modifiers) => {
      if (!scene) return;
      if (tool === 'select' || !inputEnabled) {
        selectEntity(hit);
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
      } else if (tool === 'ping') {
        if (live) ping(live.sendTransient, point);
      } else if (tool === 'wall' || tool === 'door') {
        const p = snapToGrid(point, scene);
        if (!wallStart) {
          setWallStart(p);
          setWallEnd(p);
        } else if (p.x !== wallStart.x || p.y !== wallStart.y) {
          const draft = createWall(newEntityId('wall'), wallStart, p);
          issue(tool === 'door' ? {...draft, payload:{...draft.payload, doorState:'closed'}} : draft);
          setWallStart(tool === 'door' ? null : p); // walls continue; a door is one segment
          if (tool === 'door') setWallEnd(null);
        }
      }
    },
    onPointerMove: (point) => {
      if ((tool === 'wall' || tool === 'door') && wallStart && scene) setWallEnd(snapToGrid(point, scene));
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
      if (
        e.key === 'Delete' &&
        (selected.kind === 'token' || selected.kind === 'light' || selected.kind === 'wall' || selected.kind === 'template')
      ) {
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
      <SceneBar scenes={live ? scenes : null} scene={sceneEntity ?? null} fallbackName={sceneName}
        enabled={inputEnabled} onOpen={openScene} onNew={newScene}
        onPublish={() => sceneEntity && issue(updateScene(sceneEntity, { published: !sceneEntity.published }))}
        onShow={() => sceneEntity && issue(showOnTable(sceneEntity))}
        onClear={() => sceneEntity && issue(clearTable(sceneEntity))}
        canUndo={undoState.canUndo} canRedo={undoState.canRedo} nextUndo={undoState.next}
        onUndo={() => step('undo')} onRedo={() => step('redo')}>
        <ViewportButtons controls={viewport} enabled={showMap && renderer === 'ok'} />
        <WorkspaceStatus pending={pending} zoom={zoom} status={status} />
      </SceneBar>

      <ConnectionBanner status={status} renderer={renderer} variant="gm" />

      <ToolRail railOpen={railOpen} tool={tool} inputEnabled={inputEnabled}
        templateShape={templateShape} templateSize={templateSize} terrainKind={terrainKind}
        onToggle={() => setRailOpen((v) => !v)} onChoose={chooseTool}
        canResetFog={!!fogEntity} onResetFog={() => {
          if (inputEnabled && fogEntity && window.confirm('Cover the whole scene with fog again?')) issue(fogReset(fogEntity));
        }}
        setTemplateShape={(shape) => { setTemplateShape(shape); setTemplateStart(null); }}
        setTemplateSize={setTemplateSize} setTerrainKind={setTerrainKind} />

      <main className="vtt-stage">
        <NoticeBar live={live} />
        {live && scenes?.length === 0 && !sceneEntity && <FirstRunGuidance enabled={inputEnabled}
          onNew={newScene} onPair={() => { setSidebarTab('tv'); setDockOpen(true); }} />}

        {showMap && scene && (
          <MapHost
            surface="gm"
            viewportController={viewport}
            scene={scene}
            label={`Map: ${sceneEntity?.name ?? sceneName}`}
            onViewportChange={(v) => setZoom(v.scale)}
            interaction={interaction}
            selection={selection}
            wallDraft={
              (tool === 'wall' || tool === 'door') && wallStart && wallEnd
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
              <button type="button" onClick={() => selectEntity({ kind: 'token', id: t.id })}>
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

      <WorkspaceSidebar open={dockOpen} onToggle={() => setDockOpen((v) => !v)} tab={sidebarTab} onTab={setSidebarTab}
        panels={{
          inspector: selected && ['token', 'wall', 'light', 'template'].includes(selected.kind) ?
            <Inspector entity={selected} enabled={inputEnabled} onIssue={issue} onDeleted={() => setSelection(null)}
              actors={actorEntities} {...(live ? { boot: live.boot } : {})} /> :
            <p className="vtt-empty">Select a token, light, wall or template to inspect it.</p>,
          creatures: <section><h2 className="vtt-inspector-title">Creatures</h2>
            <p className="vtt-empty">Party and encounter from the tracker. Select a token to link it in Inspector.</p>
            {actorEntities.length ? <ul>{actorEntities.map((a) => <li key={a.id}>{a.name}{a.pc ? ' (party)' : ''}</li>)}</ul> :
              <p className="vtt-empty">No creatures in the tracker yet.</p>}
            <a href="/tracker" target="_blank" rel="noopener noreferrer">Open tracker</a></section>,
          scene: <Inspector entity={null} enabled={inputEnabled} onIssue={issue} onDeleted={() => {}}
            scene={sceneEntity ?? null} fog={fogEntity ?? null} terrain={terrainEntity ?? null}
            {...(live ? { boot: live.boot } : {})} />,
          tv: live ? <><p className="vtt-empty">Pairing authorizes a display. It does not report whether that display is online.</p>
            <PairingPanel boot={live.boot} /></> : <p className="vtt-empty">Connect to a campaign to pair a TV.</p>,
          import: live ? <ImportPanel boot={live.boot} onImported={() => void refreshScenes()} /> :
            <p className="vtt-empty">Connect to a campaign to import scenes.</p>,
        }} />

      <footer className="vtt-tray" aria-label="Initiative and chat">
        <EncounterLine actors={actorEntities} />
      </footer>
    </div>
  );
}
