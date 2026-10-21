import { useEffect, useState } from 'react';
import type { GmEntity } from '../../protocol/frames';
import { lightingState,setLighting,setTokenVision,tokenVisionFeet } from '../actions/lighting';
import type { Draft } from '../gmCommands';
type Scene = Extract<GmEntity,{kind:'scene'}>;
type Token = Extract<GmEntity,{kind:'token'}>;

function VisionField({label,value,enabled,onCommit}:{label:string;value:number;enabled:boolean;onCommit(value:number):void}) {
  const [draft,setDraft] = useState(String(value));
  const [focused,setFocused] = useState(false);
  const [error,setError] = useState('');
  useEffect(() => {if (!focused) setDraft(String(value));},[value,focused]);
  return <label className="vtt-field"><span>{label}</span>
    <input type="number" aria-label={label} min={0} step={5} disabled={!enabled} value={draft}
      onFocus={() => setFocused(true)} onChange={e => setDraft(e.target.value)}
      onKeyDown={e => {if (e.key === 'Enter') {e.preventDefault();e.currentTarget.blur();}}}
      onBlur={() => {setFocused(false);const n = Number(draft);
        if (draft.trim() === '' || !Number.isFinite(n) || n < 0) {setError('Enter a distance of zero or more feet.');return;}
        setError('');if (enabled && n !== value) onCommit(n);
      }} />
    {error && <span role="alert">{error}</span>}
  </label>;
}

export function LightingControls({scene,enabled,preview,onPreview,onIssue}:{scene:Scene;enabled:boolean;preview:boolean;onPreview(on:boolean):void;onIssue(draft:Draft):unknown}) {
  const state = lightingState(scene);
  return <section className="vtt-action-panel" aria-label="Lighting and vision"><h3>Lighting and vision</h3>
    <label className="vtt-field vtt-field--toggle"><input type="checkbox" checked={state.lighting} disabled={!enabled}
      onChange={e => onIssue(setLighting(scene,e.target.checked))} />Lighting on the TV</label>
    <VisionField label="Default party vision (ft)" value={state.defaultVisionFeet} enabled={enabled}
      onCommit={feet => onIssue(setLighting(scene,state.lighting,feet))} />
    <label className="vtt-field vtt-field--toggle"><input type="checkbox" checked={preview} disabled={!state.lighting}
      onChange={e => onPreview(e.target.checked)} />Preview party vision on this GM screen</label>
    <p className="vtt-empty">Zero vision uses light only. Party tokens and visible lights provide sight; walls and closed doors block it.</p>
  </section>;
}

export function TokenVisionControls({token,scene,enabled,onIssue}:{token:Token;scene:Scene;enabled:boolean;onIssue(draft:Draft):unknown}) {
  const value = tokenVisionFeet(token,scene.gridSize);
  return <section className="vtt-action-panel" aria-label="Token vision"><h3>Token vision</h3>
    <p className="vtt-empty">Vision is used when this token is linked to a party character.</p>
    <label className="vtt-field vtt-field--toggle"><input type="checkbox" checked={value === null} disabled={!enabled}
      onChange={e => onIssue(setTokenVision(token,e.target.checked ? null : lightingState(scene).defaultVisionFeet,scene.gridSize))} />Use scene vision</label>
    {value !== null && <VisionField key={token.id} label="Token vision (ft)" value={value} enabled={enabled}
      onCommit={feet => onIssue(setTokenVision(token,feet,scene.gridSize))} />}
  </section>;
}
