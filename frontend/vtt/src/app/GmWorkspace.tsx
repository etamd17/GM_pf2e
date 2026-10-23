import { Inspector } from './Inspector';
import { onTvSceneId } from './actions/tablePointer';
import { groupMoves, pasteTokens, type GroupToken } from './controls/tokenGroups';
import { useTokenBatch } from './controls/useTokenBatch';
import { TokenActions } from './controls/TokenActions';
import { ActionDialog } from './shell/ActionDialog';
import { TOOL_KEYS, ignoresMapKey, hasMapToolFocus } from './shell/keyboard';
import { useToolShortcuts } from './shell/useToolShortcuts';
import { TurnControls } from './controls/TurnControls';
import { ConditionPicker } from './controls/ConditionPicker';
import { CreaturesPanel } from './controls/CreaturesPanel';
import { TrackerLink } from './controls/TrackerLink';
import { useCreaturePlacements } from './controls/useCreaturePlacements';
import { BestiaryPanel } from './controls/BestiaryPanel';
import { AreaEffectPanel,createAreaAttemptStore } from './controls/AreaEffectPanel';
import { LightingControls,TokenVisionControls } from './controls/LightingControls';
import { asCreature,type AddedCreature } from './actions/bestiary';
import { freeSpotNear,placeCreature,listCreatures } from './actions/creatures';
export { Inspector } from './Inspector';
import { navigateToScene } from './shell/sceneNavigation';
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
import { hitTest, snapToGrid, snapTokenCentre, type Hit, type Point } from '../scene/interaction';
import {screenToWorld,type Viewport} from '../scene/viewport';
import type {DragEvent} from 'react';
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
  deleteScene,
  duplicateScene,
  bringToFront,
  sendToBack,
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
  updateToken,
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
  const mapViewport=useRef<Viewport | null>(null);
  const stageRef=useRef<HTMLElement | null>(null);
  const draggingCreature=useRef<{actorId:string;sceneId:string} | null>(null);
  const placements=useCreaturePlacements(entities,status);
  const [areaAttempts] = useState(createAreaAttemptStore);
  const [visionPreview,setVisionPreview] = useState(false);
  const viewport = useMemo(createViewportController, []);
  const [railOpen, setRailOpen] = useState(true);
  const [dockOpen, setDockOpen] = useState(true);
  const [sidebarTab, setSidebarTab] = useSidebarTab(live?.boot.memberId);

  const [tool, setTool] = useState<Tool>('select');
  const [selection, setSelection] = useState<Hit | null>(null);
  const [groupIds,setGroupIds]=useState<Set<string>>(new Set());
  const [groupPanel,setGroupPanel]=useState(false);
  const [copied,setCopied]=useState<GroupToken[]>([]);
  const batch=useTokenBatch(status);
  const [tokenMenu, setTokenMenu] = useState<string | null>(null);
  const [shortcutHelp, setShortcutHelp] = useState(false);
  const [shortcutsEnabled, setShortcutsEnabled] = useToolShortcuts(live?.boot.memberId);
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

  const [deletingScene,setDeletingScene] = useState<{commandId:string;sceneId:string} | null>(null);
  const [deletionApplied,setDeletionApplied] = useState(false);
  const inputEnabled = !deletingScene && acceptsDurableInput(status) && renderer === 'ok' && live !== null;
  const showMap = projected !== null && !isTerminal(status);
  const scene = useMemo(
    () => (projected ? applyOverlays(projected, overlays.values() as Iterable<TokenOverlay | null>) : null),
    [projected, overlays],
  );
  const sceneEntity = useMemo(() => [...entities.values()].find((e): e is Of<'scene'> => e.kind === 'scene'), [entities]);
  const tvSceneId = onTvSceneId(entities.values());
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
  function selectEntity(hit: Hit | null, keepGroup=false) {
    setSelection(hit);
    if(!keepGroup) setGroupIds(new Set(hit?.kind==='token' ? [hit.id] : []));
    if (hit) {
      setSidebarTab('inspector');
      setDockOpen(true);
    }
  }
  const selected = selection ? (entityOf(selection.kind, selection.id) ?? null) : null;
  const menuToken = tokenMenu ? entityOf('token', tokenMenu) as Of<'token'> | undefined : undefined;
  function toggleGroup(id:string) {
    const next=new Set(groupIds);if(next.has(id)) next.delete(id);else next.add(id);
    setGroupIds(next);
    const primary=next.has(id)?id:next.values().next().value;
    selectEntity(primary?{kind:'token',id:primary}:null,true);setGroupPanel(true);
  }

  useEffect(() => {
    if (tokenMenu && (!menuToken || !showMap)) setTokenMenu(null);
  }, [tokenMenu, menuToken, showMap]);

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
      if (commandId && (draft.type.startsWith('scene.') || draft.type === 'table.show' || draft.type === 'table.clear')) tableCommands.current.add(commandId);
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
      const target = e.target;
      if (e.isComposing || e.repeat || e.keyCode === 229 || (target instanceof Element && target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="dialog"]'))) return;
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
  const sceneListGeneration = useRef(0);
  const refreshScenes = useCallback(async () => {
    if (!live) return null;
    const generation = ++sceneListGeneration.current;
    try {
      const response = await fetch(`/vtt/api/scenes?campaignId=${encodeURIComponent(live.boot.campaignId)}`, {
        credentials: 'same-origin',
      });
      const list = response.ok ? ((await response.json()) as {scenes:SceneSummary[]}).scenes : null;
      if (generation === sceneListGeneration.current) setScenes(list);
      return list;
    } catch {
      if (generation === sceneListGeneration.current) setScenes(null);
      return null;
    }
  }, [live]);

  useEffect(() => {
    void refreshScenes();
  }, [refreshScenes, sceneEntity?.version]);

  // The pointer arrives over the socket. Names/creates by another GM can be
  // refreshed on focus without polling the pointer every five seconds.
  useEffect(() => {
    const focus = () => {void refreshScenes();};
    window.addEventListener('focus',focus);
    return () => {window.removeEventListener('focus',focus);++sceneListGeneration.current;};
  },[refreshScenes]);
  const missingPointerName = useRef<string | null>(null);
  useEffect(() => {
    if (!tvSceneId || scenes?.some(s => s.id === tvSceneId)) {missingPointerName.current=null;return;}
    if (scenes && missingPointerName.current !== tvSceneId) {
      missingPointerName.current=tvSceneId;void refreshScenes();
    }
  },[tvSceneId,scenes,refreshScenes]);

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
    (sceneId: string | null) => {
      if (!live) return;
      // Opening a scene reloads the page; a change still being saved would be
      // lost with it.
      if (
        useCommands.getState().pending.length > 0 &&
        !window.confirm('A change is still being saved. Leave this scene anyway?')
      ) {
        return;
      }
      navigateToScene(sceneId);
    },
    [live],
  );

  // A created scene is opened once its command has applied.
  useEffect(() => {
    if (creatingScene && lastApplied === creatingScene.commandId) openScene(creatingScene.sceneId);
  }, [creatingScene, lastApplied, openScene]);
  useEffect(()=>{
    if(!creatingScene) return;
    const check=()=>{const notice=useCommands.getState().notice;if(notice?.kind==='rejected' && notice.commandId===creatingScene.commandId) setCreatingScene(null);};
    const stop=useCommands.subscribe(check);check();return stop;
  },[creatingScene]);

  function newScene() {
    const name = window.prompt('Name the new scene', 'New scene')?.trim();
    if (!name) return;
    const sceneId = newEntityId('scene');
    const commandId = issue(createScene(sceneId, name.slice(0, 120)));
    if (commandId) setCreatingScene({ commandId, sceneId });
  }
  function copyScene() {
    if (!inputEnabled || !sceneEntity || creatingScene) return;
    const name=window.prompt('Name the duplicate scene (the TV stays unchanged)', `${sceneEntity.name} (copy)`)?.trim().slice(0,120);
    if (!name) return;
    const sceneId=newEntityId('scene');
    const commandId=issue(duplicateScene(sceneEntity,sceneId,name));
    if(commandId) setCreatingScene({commandId,sceneId});
  }

  function removeScene() {
    if(!inputEnabled || !sceneEntity || tvSceneId === undefined || tvSceneId === sceneEntity.id) return;
    const commandId=issue(deleteScene(sceneEntity));
    if(commandId) {setDeletionApplied(false);setDeletingScene({commandId,sceneId:sceneEntity.id});}
  }
  useEffect(()=>{
    if(!deletingScene) return;
    const inspect=()=>{
      const state=useCommands.getState();
      if(state.lastApplied === deletingScene.commandId) setDeletionApplied(true);
      else if(state.notice?.kind === 'rejected' && state.notice.commandId === deletingScene.commandId) setDeletingScene(null);
    };
    const stop=useCommands.subscribe(inspect);inspect();return stop;
  },[deletingScene]);
  useEffect(()=>{
    if(!deletingScene || !deletionApplied) return;
    let current=true;
    void refreshScenes().then(list=>{
      if(!current) return;
      setDeletingScene(null);
      if(list) openScene(list.find(s=>s.id !== deletingScene.sceneId)?.id ?? null);
      // A failed list read leaves the deletion notice and picker available;
      // it must not guess a destination or resend the delete.
    });
    return ()=>{current=false;};
  },[deletingScene,deletionApplied,refreshScenes,openScene]);
  // A valid GM snapshot has no scene entity iff its sceneId is null. Resync
  // clears and terminal revocation are excluded by the live status check.
  const sceneDeleted = !!live?.boot.sceneId && status === 'live' && !sceneEntity;

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
    canDragToken: (id) => {const token=entityOf('token',id) as Of<'token'>|undefined;return inputEnabled && !batch.busy && !(groupIds.has(id) && selectionPending) && tool==='select' && !!token && !token.locked;},
    onTokenDrop: (id, x, y) => {
      const token = entityOf('token', id) as Of<'token'> | undefined;
      if (!token || token.locked || !inputEnabled || batch.busy) return;
      if (token.x === x && token.y === y) return;
      if(groupIds.has(id) && groupIds.size>1) {moveSelection(x-token.x,y-token.y);selectEntity({kind:'token',id},true);return;}
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
      if (ignoresMapKey(e)) return false;
      if (e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey)) {
        if (selected?.kind !== 'token') return false;
        setTokenMenu(selected.id);
        return true;
      }
      if (e.key === 'Escape') {
        setWallStart(null);
        setWallEnd(null);
        setTemplateStart(null);
        setSelection(null);
        setGroupIds(new Set());
        return true;
      }
      if (!inputEnabled || !selected || !scene) return false;
      if(selected.kind==='token' && groupIds.size>1 && groupIds.has(selected.id) && e.key.startsWith('Arrow')) {
        const g=scene.gridSize;
        moveSelection(e.key==='ArrowLeft'?-g:e.key==='ArrowRight'?g:0,e.key==='ArrowUp'?-g:e.key==='ArrowDown'?g:0);return true;
      }
      // Delete only: Backspace is too easy to hit by accident on a map.
      if (
        e.key === 'Delete' &&
        (selected.kind === 'token' || selected.kind === 'light' || selected.kind === 'wall' || selected.kind === 'template')
      ) {
        if(groupIds.size>1) {setAnnouncement('Open an individual token menu to delete it; group Delete is not supported.');return true;}
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
  const allEntities = [...entities.values()];
  const linkedActor = selected?.kind === 'token' ? actorEntities.find(a => a.id === selected.actorId) : undefined;
  function placementScene() {
    const host=stageRef.current?.querySelector<HTMLElement>('.vtt-map-host');
    return {host,viewport:mapViewport.current};
  }
  function placeActor(actorId:string,dropPoint?:Point) {
    // Re-read the mirror at the drop/click boundary; never place a stale row.
    const creature=listCreatures(useEntities.getState().entities.values()).find(c=>c.actorId===actorId);
    if(!inputEnabled || !scene || !sceneEntity || !creature || placements.isPending(actorId)) return;
    if(creature.onScene.length && !window.confirm(`${creature.name} is already on this map. Place another linked token?`)) return;
    const {host,viewport:currentView}=placementScene();
    const rect=host?.getBoundingClientRect();
    const centre=currentView && rect ? screenToWorld(currentView,{x:rect.width/2,y:rect.height/2}) : {x:scene.width/2,y:scene.height/2};
    const point=dropPoint ?? freeSpotNear(centre,creature.size,scene);
    const placement=placeCreature(creature,point,scene);
    const commandId=issue(placement.draft,overlayFor(placement.draft));
    if(commandId) {
      placements.register({actorId,sceneId:sceneEntity.id,tokenId:placement.tokenId,commandId});
      setAnnouncement(`Placing ${creature.name}.`);
    }
  }
  function startCreatureDrag(actorId:string,event:DragEvent<HTMLButtonElement>) {
    if(!inputEnabled || !sceneEntity || placements.isPending(actorId) || !entityOf('actor',actorId)) {event.preventDefault();return;}
    chooseTool('select');
    draggingCreature.current={actorId,sceneId:sceneEntity.id};
    event.dataTransfer.setData('application/x-vtt-creature',actorId);event.dataTransfer.effectAllowed='copy';
  }
  function dragOverCreature(event:DragEvent<HTMLElement>) {
    if(inputEnabled && sceneEntity && draggingCreature.current?.sceneId===sceneEntity.id &&
      event.target instanceof Element && event.target.closest('.vtt-map-host') && event.dataTransfer.types.includes('application/x-vtt-creature')) {
      event.preventDefault();event.dataTransfer.dropEffect='copy';
    }
  }
  function dropCreature(event:DragEvent<HTMLElement>) {
    const dragged=draggingCreature.current;draggingCreature.current=null;
    const {host,viewport:currentView}=placementScene();
    if(!dragged || dragged.sceneId!==sceneEntity?.id || !host || !currentView ||
      !(event.target instanceof Element) || !event.target.closest('.vtt-map-host') ||
      event.dataTransfer.getData('application/x-vtt-creature')!==dragged.actorId) return;
    event.preventDefault();
    const rect=host.getBoundingClientRect();
    placeActor(dragged.actorId,screenToWorld(currentView,{x:event.clientX-rect.left,y:event.clientY-rect.top}));
  }
  function placeAddedCreature(added:AddedCreature):boolean {
    if (!inputEnabled || !scene || !sceneEntity) return false;
    const creature = asCreature(added);
    const point = freeSpotNear({x:scene.width / 2,y:scene.height / 2},creature.size,scene);
    const placement=placeCreature(creature,point,scene);
    const commandId=issue(placement.draft,overlayFor(placement.draft));
    if(commandId) placements.register({actorId:creature.actorId,sceneId:sceneEntity.id,tokenId:placement.tokenId,commandId});
    return !!commandId;
  }

  function pointerToken(event: { target: EventTarget; clientX: number; clientY: number }) {
    if (!scene || !showMap || !(event.target instanceof Element) || !event.target.closest('.vtt-map-host')) return null;
    const {host, viewport:currentView} = placementScene();
    if (!host || !currentView) return null;
    const rect = host.getBoundingClientRect();
    const hit = hitTest(scene, screenToWorld(currentView, {x:event.clientX-rect.left, y:event.clientY-rect.top}), currentView.scale);
    return hit?.kind === 'token' ? entityOf('token', hit.id) as Of<'token'> | undefined : null;
  }
  function openSheet(token: Of<'token'>) {
    const actor = token.actorId ? entityOf('actor', token.actorId) as Of<'actor'> | undefined : undefined;
    if (actor?.sheetUrl) window.open(actor.sheetUrl, '_blank', 'noopener,noreferrer');
  }
  function changeMenuToken(patch: Partial<Pick<Of<'token'>, 'hidden'|'locked'|'size'|'actorId'>>) {
    // Re-read at the action boundary; a mirror update can delete or replace
    // the menu's token without this render having painted yet.
    const token = tokenMenu ? entityOf('token', tokenMenu) as Of<'token'> | undefined : undefined;
    if (!inputEnabled || !token || (patch.actorId && !entityOf('actor', patch.actorId))) return;
    const draft = updateToken(token, patch);
    issue(draft, overlayFor(draft));
    setTokenMenu(null);
  }
  function selectedTokens() {
    return [...groupIds].map(id=>entityOf('token',id) as GroupToken | undefined).filter((t):t is GroupToken=>!!t);
  }
  const selectionPending=[...overlays.values()].some(o=>o && groupIds.has(o.id));
  function moveSelection(dx:number,dy:number) {
    if(!inputEnabled || !sceneEntity || batch.busy || selectionPending) return;
    const tokens=selectedTokens();
    const skipped=[...groupIds].filter(id=>!tokens.some(t=>t.id===id && !t.locked)).map(id=>`${tokens.find(t=>t.id===id)?.label || 'Missing token'} [${id}]`);
    const drafts=groupMoves(tokens,sceneEntity,dx,dy);
    if(!drafts.length && !skipped.length) {setAnnouncement('The selection cannot move farther inside this scene.');return;}
    batch.run(drafts.map(d=>({draft:d,name:`${tokens.find(t=>t.id===d.payload.id)?.label || 'Unnamed'} [${String(d.payload.id)}]`})),skipped,d=>issue(d,overlayFor(d)));
  }
  function copySelection() {
    if(!inputEnabled || batch.busy || selectionPending) return;
    const tokens=selectedTokens();setCopied(structuredClone(tokens));
    setAnnouncement(`Copied ${tokens.length} tokens in this page. Pasting keeps their tracker links, locks and hidden flags.`);
  }
  function pasteSelection() {
    if(!inputEnabled || !sceneEntity || !copied.length || batch.busy) return;
    const drafts=pasteTokens(copied,sceneEntity);
    batch.run(drafts.map((draft,i)=>({draft,name:`${copied[i]?.label || 'Unnamed token'} [${String(draft.payload.id)}]`})),[],d=>{
      const overlay=overlayFor(d);
      return issue(d,overlay && 'create' in overlay ? {...overlay,create:{...overlay.create,hidden:d.payload.hidden===true}} : overlay);
    });
  }

  return (
    <div className={`vtt-gm ${railOpen ? '' : 'is-rail-collapsed'} ${dockOpen ? '' : 'is-dock-collapsed'}`}
      onKeyDownCapture={event => {
        const e = event.nativeEvent;
        if((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && !e.isComposing && e.keyCode!==229 && !e.repeat && hasMapToolFocus(e.target)
          && e.target instanceof Element && !e.target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])') && ['c','v'].includes(e.key.toLowerCase())) {
          event.preventDefault();event.stopPropagation();if(e.key.toLowerCase()==='c') copySelection();else pasteSelection();return;
        }
        if (ignoresMapKey(e) || !hasMapToolFocus(e.target)) return;
        const focusedToken = e.target instanceof Element ? e.target.closest<HTMLButtonElement>('button[data-token-id]')?.dataset.tokenId : undefined;
        if (focusedToken && e.key === 'Escape') {
          event.preventDefault(); event.stopPropagation(); placementScene().host?.focus(); return;
        }
        if (focusedToken && (e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey))) {
          event.preventDefault(); event.stopPropagation(); selectEntity({kind:'token', id:focusedToken}); setTokenMenu(focusedToken); return;
        }
        if (shortcutsEnabled && (e.key === '?' || TOOL_KEYS[e.key.toLowerCase()])) {
          event.preventDefault(); event.stopPropagation();
          if (e.key === '?') setShortcutHelp(true);
          else if (TOOL_KEYS[e.key.toLowerCase()] === 'select' || inputEnabled) chooseTool(TOOL_KEYS[e.key.toLowerCase()]!);
          return;
        }
        if (e.key === 'Enter' && e.target instanceof Element && e.target.classList.contains('vtt-map-host') && tool === 'token' && inputEnabled && scene) {
          const {host,viewport:currentView} = placementScene();
          const rect = host?.getBoundingClientRect();
          const point = currentView && rect ? screenToWorld(currentView, {x:rect.width/2,y:rect.height/2}) : {x:scene.width/2,y:scene.height/2};
          interaction.onClick?.(point, null);
          event.preventDefault(); event.stopPropagation(); return;
        }
        if (e.target instanceof Element && e.target.closest('.vtt-map-host, .vtt-token-selection') && interaction.onKey?.(e)) { event.preventDefault(); event.stopPropagation(); }
      }}>
      <SceneBar scenes={live ? scenes : null} onTvId={tvSceneId} scene={sceneEntity ?? null} fallbackName={sceneName}
        enabled={inputEnabled} onOpen={openScene} onNew={newScene} onDelete={removeScene}
        onDuplicate={copyScene}
        onRename={name => sceneEntity && issue(updateScene(sceneEntity, { name }))}
        onShow={() => sceneEntity && issue(showOnTable(sceneEntity))}
        onClear={() => sceneEntity && issue(clearTable(sceneEntity))}
        canUndo={undoState.canUndo} canRedo={undoState.canRedo} nextUndo={undoState.next}
        onUndo={() => step('undo')} onRedo={() => step('redo')}>
        <ViewportButtons controls={viewport} enabled={showMap && renderer === 'ok'} />
        <button type="button" onClick={() => setShortcutHelp(true)}>Keyboard help</button>
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

      <main className="vtt-stage" ref={stageRef} onDragOver={dragOverCreature} onDrop={dropCreature}
        onPointerDownCapture={event=>{
          if(event.button===0 && event.shiftKey && tool==='select') {
            const token=pointerToken(event);if(token) {event.preventDefault();event.stopPropagation();toggleGroup(token.id);}
          }
        }}
        onContextMenu={event => {
          const token = pointerToken(event);
          if (token) {event.preventDefault(); chooseTool('select'); selectEntity({kind:'token',id:token.id}); setTokenMenu(token.id);}
        }} onDoubleClick={event => { const token = tool === 'select' ? pointerToken(event) : null; if (token) openSheet(token); }}>
        <NoticeBar live={live} />
        {(sceneDeleted || deletionApplied) && <div className="vtt-notice" role="status">
          <span>This scene was deleted. Choose another scene in the Editing picker, or create a new scene.</span>
        </div>}
        {live && scenes?.length === 0 && !sceneEntity && <FirstRunGuidance enabled={inputEnabled}
          onNew={newScene} onPair={() => { setSidebarTab('tv'); setDockOpen(true); }} />}

        {showMap && scene && (
          <MapHost
            surface="gm"
            viewportController={viewport}
            visionPreview={visionPreview}
            scene={scene}
            label={`Map: ${sceneEntity?.name ?? sceneName}`}
            onViewportChange={(v) => {mapViewport.current=v;setZoom(v.scale);}}
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
        {showMap && selected?.kind === 'token' && <div className="vtt-token-selection">
          <span>{selected.label || 'Unnamed token'}</span>
          <button type="button" aria-label="Token actions" aria-haspopup="dialog" onClick={() => setTokenMenu(selected.id)}>...</button>
        </div>}
        {showMap && <button type="button" className="vtt-selection-launch" aria-expanded={groupPanel} onClick={()=>{setGroupPanel(v=>!v);setSidebarTab('inspector');setDockOpen(true);}}>Select tokens ({selectedTokens().length})</button>}
        {/* The canvas has an off-screen mirror for screen readers. */}
        <ul className="vtt-visually-hidden" aria-label="Tokens on the map">
          {visibleTokens.map((t) => (
            <li key={t.id}>
              <button type="button" data-token-id={t.id} onClick={() => selectEntity({ kind: 'token', id: t.id })}
                onContextMenu={e => { e.preventDefault(); selectEntity({kind:'token',id:t.id}); setTokenMenu(t.id); }}
                onKeyDown={e => {
                  if (!ignoresMapKey(e.nativeEvent) && (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10'))) {
                    e.preventDefault(); selectEntity({kind:'token',id:t.id}); setTokenMenu(t.id);
                  }
                }}>
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
          inspector: <>
{showMap && <section className="vtt-group-controls" aria-label="Token selection">
          <button type="button" aria-expanded={groupPanel} onClick={()=>setGroupPanel(v=>!v)}>{groupPanel ? 'Hide selection options' : 'Show selection options'} ({selectedTokens().length})</button>
          {groupPanel && <>
            <p>Shift-click tokens or use this list. Other selected tokens follow when the dragged token is dropped. Locked tokens stay put.</p>
            <div className="vtt-group-list">{visibleTokens.map(t=><label key={t.id}>
              <input type="checkbox" checked={groupIds.has(t.id)} onChange={()=>toggleGroup(t.id)}/>
              {t.label || 'Unnamed'} [{t.id}] {(entityOf('token',t.id) as GroupToken | undefined)?.locked?'(locked)':''} {t.hidden?'(hidden)':''}
            </label>)}</div>
            <button type="button" onClick={()=>{setGroupIds(new Set(visibleTokens.map(t=>t.id)));const first=visibleTokens[0];selectEntity(first?{kind:'token',id:first.id}:null,true);}}>Select all</button>
            <button type="button" onClick={()=>selectEntity(null)}>Clear selection</button>
            <div className="vtt-group-buttons">{([['Left',-1,0],['Right',1,0],['Up',0,-1],['Down',0,1]] as const).map(([label,x,y])=>
              <button type="button" key={label} disabled={!inputEnabled || batch.busy || selectionPending || !groupIds.size} onClick={()=>moveSelection(x*(sceneEntity?.gridSize ?? 0),y*(sceneEntity?.gridSize ?? 0))}>Move selection {label.toLowerCase()}</button>)}</div>
            <button type="button" disabled={!inputEnabled || batch.busy || selectionPending || !groupIds.size} onClick={copySelection}>Copy tokens</button>
            <button type="button" disabled={!inputEnabled || batch.busy || !copied.length} onClick={pasteSelection}>Paste tokens ({copied.length})</button>
            <p>Copy stays in this page; reload clears it. Copies keep locks, hidden state and tracker links. Move and Copy wait for command receipts and the scene mirror. Undo reverses one token at a time.</p>
          </>}
          {!!batch.rows.length && <div role="status" className="vtt-batch-results">
            <p>{batch.rows.filter(r=>r.result==='Applied').length} applied; {batch.rows.filter(r=>r.result.startsWith('Rejected')).length} rejected; {batch.rows.filter(r=>r.result.startsWith('Skipped')).length} skipped; {batch.rows.filter(r=>r.result==='Waiting').length} waiting; {batch.rows.filter(r=>r.result==='Not queued').length} not queued; {batch.rows.filter(r=>r.result.startsWith('Unknown')).length} unknown. Partial results are not atomic; nothing is retried automatically.</p>
            <ul>{batch.rows.map((r,i)=><li key={i}>{r.name}: {r.result}</li>)}</ul>
          </div>}
        </section>}
{selected && ['token', 'wall', 'light', 'template'].includes(selected.kind) ?
            <><Inspector entity={selected} enabled={inputEnabled} onIssue={issue} onDeleted={() => setSelection(null)}
              actors={actorEntities} {...(live ? { boot: live.boot } : {})} />
              {live && linkedActor && <ConditionPicker key={`${live.boot.campaignId}:${linkedActor.id}`} boot={live.boot} actor={linkedActor} enabled={inputEnabled} />}
              {selected.kind === 'token' && sceneEntity && <TokenVisionControls key={selected.id} token={selected} scene={sceneEntity} enabled={inputEnabled} onIssue={issue} />}
              {live && selected.kind === 'template' && sceneEntity && <AreaEffectPanel attempts={areaAttempts} key={`${sceneEntity.id}:${selected.id}`} boot={live.boot} template={selected} scene={sceneEntity} entities={allEntities} enabled={inputEnabled} />}
            </> :
            <p className="vtt-empty">Select a token, light, wall or template to inspect it.</p>}</>,
          creatures: <section>
            {!isTerminal(status) && <CreaturesPanel entities={allEntities} enabled={inputEnabled && !!sceneEntity}
              pendingActorIds={placements.pendingActorIds} onPlace={placeActor} onDragStart={startCreatureDrag} onDragEnd={()=>{draggingCreature.current=null;}}/>}
            {live && <TrackerLink boot={live.boot}/>}
            {live && !isTerminal(status) && <BestiaryPanel key={live.boot.campaignId} boot={live.boot} entities={allEntities} enabled={inputEnabled && !!sceneEntity} onPlace={placeAddedCreature} />}</section>,
          scene: <><Inspector entity={null} enabled={inputEnabled} onIssue={issue} onDeleted={() => {}}
            scene={sceneEntity ?? null} fog={fogEntity ?? null} terrain={terrainEntity ?? null}
            {...(live ? { boot: live.boot } : {})} />
            {sceneEntity && <LightingControls key={sceneEntity.id} scene={sceneEntity} enabled={inputEnabled} preview={visionPreview} onPreview={setVisionPreview} onIssue={issue} />}</>,
          tv: live ? <><p className="vtt-empty">Pairing authorizes a display. It does not report whether that display is online.</p>
            <PairingPanel boot={live.boot} /></> : <p className="vtt-empty">Connect to a campaign to pair a TV.</p>,
          import: live ? <ImportPanel boot={live.boot} onImported={() => void refreshScenes()} /> :
            <p className="vtt-empty">Connect to a campaign to import scenes.</p>,
        }} />

      <footer className="vtt-tray" aria-label="Encounter">
        {live ? <TurnControls key={live.boot.campaignId} boot={live.boot} entities={allEntities} enabled={inputEnabled} /> : <EncounterLine actors={actorEntities} />}
      </footer>
      {menuToken && showMap && <TokenActions token={menuToken} actors={actorEntities} enabled={inputEnabled}
        canFront={bringToFront(menuToken,allEntities.filter((e):e is Of<'token'>=>e.kind==='token')) !== null}
        canBack={sendToBack(menuToken,allEntities.filter((e):e is Of<'token'>=>e.kind==='token')) !== null}
        onOrder={direction=>{
          const token=entityOf('token',menuToken.id) as Of<'token'> | undefined;
          if(!inputEnabled || !token) return;
          const tokens=[...useEntities.getState().entities.values()].filter((e):e is Of<'token'>=>e.kind==='token');
          const draft=direction==='front' ? bringToFront(token,tokens) : sendToBack(token,tokens);
          if(draft) issue(draft,overlayFor(draft));setTokenMenu(null);
        }}
        onChange={changeMenuToken} onClose={() => setTokenMenu(null)} onDelete={() => {
          const token = tokenMenu ? entityOf('token',tokenMenu) as Of<'token'> | undefined : undefined;
          if (token && inputEnabled) {issue(deleteEntity(token), {kind:'token',id:token.id,deleted:true});setSelection(null);setTokenMenu(null);}
        }} />}
      {shortcutHelp && <ActionDialog title="Keyboard help" onClose={() => setShortcutHelp(false)}>
        <label><input type="checkbox" checked={shortcutsEnabled} onChange={e => setShortcutsEnabled(e.target.checked)}/> Enable single-key tool shortcuts</label>
        <p>With the map or tools focused: V Select, P Place token, M Measure, A Area template, W Wall, F Reveal fog, R Terrain, ? Help.</p>
        <p>Focus the map and press Enter to place a token at the view centre. Tab to the token list to select one. Menu or Shift+F10 opens its actions; Escape closes. Escape from the token list returns to the map. Arrow keys move an unlocked selection; Delete removes it. Ctrl/Cmd+Z undoes; Shift+Ctrl/Cmd+Z redoes.</p>
        <p>Tool shortcuts do not run while typing. Disable them above if you use single keys for assistive controls. All tools remain available as buttons.</p>
      </ActionDialog>}
    </div>
  );
}
