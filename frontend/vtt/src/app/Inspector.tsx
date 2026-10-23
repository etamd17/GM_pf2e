import { useEffect, useState } from 'react';
import type { GmEntity } from '../protocol/frames';
import type { GmBoot } from '../live/boot';
import { conditionLabel } from '../scene/adapt';
import { ImagePicker } from './ImagePicker';
import { TOKEN_COLORS, deleteEntity, fogReset, fogUpdate, terrainReset, updateScene, updateToken, updateLight, templateUpdate, setDoorState, type DoorState, type Draft, type TokenOverlay } from './gmCommands';
const TEMPLATE_COLORS = [0x75c5eb, ...TOKEN_COLORS] as const;
const TEMPLATE_SHAPES = [
  { id: 'burst', label: 'Burst' },
  { id: 'emanation', label: 'Emanation' },
  { id: 'cone', label: 'Cone' },
  { id: 'line', label: 'Line' },
] as const;
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
  // Tracker actors a token can link to (parity P3).
  actors?: ReadonlyArray<Extract<GmEntity, { kind: 'actor' }>>;
}

const mediaUrl = (boot: Pick<GmBoot, 'campaignId'>, id: string | null) =>
  id ? `/vtt/media/${encodeURIComponent(boot.campaignId)}/${encodeURIComponent(id)}` : null;

// The selected entity's fields. A text field commits on blur or Enter, never
// on every keystroke; a field the GM is editing is not overwritten by a frame
// until they leave it (the key resets the field when the entity's version
// changes while it is not focused).
// The tray's one line about the tracker (parity P3). The mirror carries no
// initiative order, so this says only how many are fighting and whose turn it is.
// Which tracker combatant or party character drives this token's HP and
// conditions (parity P3). A link to an actor that is gone stays listed, so it
// is visible and can be cleared.
function TrackerLink({
  entity,
  actors,
  enabled,
  onIssue,
}: {
  entity: Extract<GmEntity, { kind: 'token' }>;
  actors: ReadonlyArray<Extract<GmEntity, { kind: 'actor' }>>;
  enabled: boolean;
  onIssue: InspectorProps['onIssue'];
}) {
  const linked = entity.actorId ? actors.find((a) => a.id === entity.actorId) : undefined;
  return (
    <>
      <label className="vtt-field">
        <span>Tracker link</span>
        <select
          value={entity.actorId ?? ''}
          disabled={!enabled}
          onChange={(e) => {
            const actorId = e.target.value || null;
            if (actorId !== entity.actorId) onIssue(updateToken(entity, { actorId }));
          }}
        >
          <option value="">None</option>
          {actors.map((a) => (
            <option key={a.id} value={a.id}>
              {(a.name || 'Unnamed') + (a.pc ? ' (party)' : '') + (a.hidden ? ' (hidden)' : '')}
            </option>
          ))}
          {entity.actorId && !linked && <option value={entity.actorId}>Not in the tracker now</option>}
        </select>
      </label>
      {linked && (
        <p className="vtt-empty">
          {linked.hp} / {linked.maxHp} HP
          {linked.conditions.length ? ` - ${linked.conditions.map((c) => conditionLabel(c.name, c.value ?? null)).join(', ')}` : ''}
          {linked.hidden ? ' - hidden in the tracker, so not on the table' : ''}
        </p>
      )}
    </>
  );
}

export function Inspector({ entity, enabled, onIssue, onDeleted, scene, boot, fog, terrain, actors = [] }: InspectorProps) {
  if (!entity || entity.kind === 'scene' || entity.kind === 'fog' || entity.kind === 'terrain' || entity.kind === 'actor' || entity.kind === 'table') {
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
          <TrackerLink entity={entity} actors={actors} enabled={enabled} onIssue={onIssue} />
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
