import type { GmEntity } from '../../protocol/frames';
import { ActionDialog } from '../shell/ActionDialog';
type Token = Extract<GmEntity, { kind: 'token' }>;
type Actor = Extract<GmEntity, { kind: 'actor' }>;
export function TokenActions({ token, actors, enabled, onChange, onDelete, onClose }: {
  token: Token; actors: readonly Actor[]; enabled: boolean;
  onChange(patch: Partial<Pick<Token, 'hidden' | 'locked' | 'size' | 'actorId'>>): void;
  onDelete(): void; onClose(): void;
}) {
  const actor = actors.find(a => a.id === token.actorId);
  return <ActionDialog title={`Token actions: ${token.label || 'Unnamed token'}`} onClose={onClose}>
    <button type="button" disabled={!enabled} onClick={() => onChange({ hidden: !token.hidden })}>
      {token.hidden ? 'Show on TV' : 'Hide from TV'}</button>
    {actor?.hidden && <p>Hidden in the tracker; showing this token does not reveal it on the TV.</p>}
    <button type="button" disabled={!enabled} onClick={() => onChange({ locked: !token.locked })}>
      {token.locked ? 'Unlock token' : 'Lock token'}</button>
    <label className="vtt-field">Size (squares)<select value={token.size} disabled={!enabled}
      onChange={e => onChange({ size: Number(e.target.value) })}>
      {[0.5, 1, 2, 3, 4].map(size => <option key={size} value={size}>{size}</option>)}
      {![0.5, 1, 2, 3, 4].includes(token.size) && <option value={token.size}>{token.size}</option>}
    </select></label>
    <label className="vtt-field">Tracker link<select value={token.actorId ?? ''} disabled={!enabled}
      onChange={e => onChange({ actorId: e.target.value || null })}>
      <option value="">None</option>
      {actors.map(a => <option key={a.id} value={a.id}>{a.name || 'Unnamed'}{a.hidden ? ' (hidden)' : ''}</option>)}
      {token.actorId && !actor && <option value={token.actorId}>Not in the tracker now</option>}
    </select></label>
    {actor?.sheetUrl && <a href={actor.sheetUrl} target="_blank" rel="noopener noreferrer">Open sheet</a>}
    <button type="button" disabled={!enabled} className="vtt-danger" onClick={() => {
      if (window.confirm(`Delete ${token.label || 'this token'} from the map? The tracker creature stays.`)) onDelete();
    }}>Delete token</button>
  </ActionDialog>;
}
