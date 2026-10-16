import { useCallback, useEffect, useMemo, useState } from 'react';
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
  moveToken,
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
import { ImportPanel, PairingPanel } from './GmPanels';
import { NoticeBar } from './NoticeBar';
import { inverseFor, overlayFor, targetOf } from './undo';

interface Props {
  sceneName: string;
  // The live wiring when served by Flask; null on the Vite dev server, where
  // the tools are shown but have nothing to send to.
  live?: GmLive | null;
}

type Tool = 'select' | 'token' | 'light' | 'wall';

const TOOLS: ReadonlyArray<{ id: Tool; label: string; hint: string }> = [
  { id: 'select', label: 'Select', hint: 'Select and drag tokens; arrows move the selected token' },
  { id: 'token', label: 'Token', hint: 'Click to place a token' },
  { id: 'light', label: 'Light', hint: 'Click to place a light' },
  { id: 'wall', label: 'Walls', hint: 'Click to start a wall, click again to end it; Esc stops' },
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
  const selected = selection ? (entityOf(selection.kind, selection.id) ?? null) : null;

  // A selection whose entity is gone (deleted here or elsewhere) is dropped.
  useEffect(() => {
    if (selection && !entityOf(selection.kind, selection.id)) setSelection(null);
  }, [entities, selection]);

  // Every edit goes through here: it is issued, and its inverse is recorded so
  // it can be undone once applied (undo.ts).
  const send = useCallback(
    (draft: Draft, overlay: TokenOverlay | null, mode: 'edit' | 'undo' | 'redo') => {
      if (!live) return null;
      const target = targetOf(draft);
      const before = target ? entityOf(target.kind, target.id) : undefined;
      const commandId = live.issue(draft, overlay);
      const inverse = inverseFor(draft, before);
      if (commandId && inverse) live.undo.track(commandId, inverse, mode);
      return commandId;
    },
    [live],
  );
  const issue = useCallback(
    (draft: Draft, overlay: TokenOverlay | null = null) => send(draft, overlay, 'edit'),
    [send],
  );

  const step = useCallback(
    (direction: 'undo' | 'redo') => {
      if (!live || !inputEnabled) return;
      const entry = direction === 'undo' ? live.undo.popUndo() : live.undo.popRedo();
      if (!entry) return;
      const draft = entry.make(entityOf(entry.kind, entry.id));
      if (!draft) {
        useCommands.getState().notify({
          kind: 'info',
          text: `Cannot ${direction} the last ${entry.label}: it was changed or removed since.`,
        });
        return;
      }
      send(draft, overlayFor(draft), direction);
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
    onClick: (point, hit) => {
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
    },
    onKey: (e) => {
      if (e.key === 'Escape') {
        setWallStart(null);
        setWallEnd(null);
        setSelection(null);
        return true;
      }
      if (!inputEnabled || !selected || !scene) return false;
      // Delete only: Backspace is too easy to hit by accident on a map.
      if (e.key === 'Delete' && selected.kind !== 'scene') {
        issue(deleteEntity(selected), selected.kind === 'token' ? { kind: 'token', id: selected.id, deleted: true } : null);
        setSelection(null);
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
            wallDraft={tool === 'wall' && wallStart && wallEnd ? { a: wallStart, b: wallEnd } : null}
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
}

// The selected entity's fields. A text field commits on blur or Enter, never
// on every keystroke; a field the GM is editing is not overwritten by a frame
// until they leave it (the key resets the field when the entity's version
// changes while it is not focused).
export function Inspector({ entity, enabled, onIssue, onDeleted }: InspectorProps) {
  if (!entity || entity.kind === 'scene') return <p className="vtt-empty">Select a token, light or wall to inspect it.</p>;
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
