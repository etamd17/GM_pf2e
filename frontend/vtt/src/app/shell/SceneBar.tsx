import { useState,type ReactNode } from 'react';
import type { GmEntity } from '../../protocol/frames';
interface Summary { id: string; name: string; published: boolean; onTable: boolean }
interface Props {
  scenes: readonly Summary[] | null;
  scene: Extract<GmEntity, { kind: 'scene' }> | null;
  fallbackName: string; enabled: boolean; canUndo: boolean; canRedo: boolean; nextUndo: string | null;
  onOpen(id: string): void; onNew(): void; onRename(name:string): void; onDelete():void; onShow(): void; onClear(): void;
  onUndo(): void; onRedo(): void; children: ReactNode;
}
function SceneMenu({scene,enabled,onRename,onDelete,onClear,known,onTv}:{scene:NonNullable<Props['scene']>;enabled:boolean;onRename:Props['onRename'];onDelete:Props['onDelete'];onClear:Props['onClear'];known:boolean;onTv:boolean}) {
  const [name,setName]=useState(scene.name);
  const trimmed=name.trim();
  return <details className="vtt-scene-menu"><summary>Scene menu</summary>
    <div className="vtt-scene-menu-content"><form onSubmit={event=>{event.preventDefault();if(enabled && trimmed && trimmed !== scene.name) onRename(trimmed);}}>
      <label className="vtt-field">Scene name<input value={name} maxLength={120} disabled={!enabled}
        onChange={event=>setName(event.target.value)} /></label>
      <button className="vtt-topbar-btn" disabled={!enabled || !trimmed || trimmed === scene.name}>Save name</button>
    </form>
    <button type="button" className="vtt-topbar-btn" disabled={!enabled || !known || onTv} onClick={()=>{
      if(enabled && known && !onTv && window.confirm(`Delete “${scene.name}”? This cannot be undone. Uploaded images stay.`)) onDelete();
    }}>Delete scene</button>
    {onTv && <><p className="vtt-empty">Clear the TV before deleting this scene.</p>
      <button type="button" className="vtt-topbar-btn" disabled={!enabled || !known} onClick={onClear}>Clear TV before deleting</button></>}
    </div>
  </details>;
}
// Navigation and TV writes deliberately remain separate callbacks.
export function SceneBar(p: Props) {
  const onTv=p.scenes?.find(s=>s.onTable);
  const known=p.scenes !== null;
  function show() {
    if(!p.enabled || !p.scene || !known) return;
    if(onTv && onTv.id !== p.scene.id && !window.confirm(`Replace “${onTv.name}” on the TV with “${p.scene.name}”?`)) return;
    p.onShow();
  }
  return <header className="vtt-topbar">
    <span className="vtt-brand">VTT</span>
    <label className="vtt-editing">Editing {p.scenes ? <select className="vtt-scene-picker" aria-label="Editing" value={p.scene?.id ?? ''}
      onChange={e=>e.target.value && e.target.value !== p.scene?.id && p.onOpen(e.target.value)}>
      {!p.scene && <option value="">No scene</option>}
      {p.scene && !p.scenes.some(s=>s.id===p.scene!.id) && <option value={p.scene.id}>{p.scene.name}</option>}
      {p.scenes.map(s=><option key={s.id} value={s.id}>{s.name}{s.onTable ? ' (On TV)' : ''}</option>)}
    </select> : <span className="vtt-scene-name">{p.scene?.name ?? p.fallbackName}</span>}</label>
    <span className="vtt-tv-scene" aria-label="On TV" role="status">On TV: {known ? (onTv?.name ?? 'Nothing') : 'Unavailable'}</span>
    <button className="vtt-topbar-btn" disabled={!p.enabled} onClick={p.onNew}>New scene</button>
    {p.scene && <>
      <button className="vtt-topbar-btn" disabled={!p.enabled || !known} onClick={show}>Show on TV</button>
      <button className="vtt-topbar-btn" disabled={!p.enabled || !known || !onTv} onClick={p.onClear}>Clear TV</button>
      <SceneMenu key={p.scene.id} scene={p.scene} enabled={p.enabled} onRename={p.onRename} onDelete={p.onDelete} onClear={p.onClear} known={known} onTv={onTv?.id === p.scene.id}/>
    </>}
    <button className="vtt-topbar-btn" disabled={!p.enabled || !p.canUndo} onClick={p.onUndo}
      title={p.nextUndo ? `Undo ${p.nextUndo} (Ctrl+Z)` : 'Undo (Ctrl+Z)'}>Undo</button>
    <button className="vtt-topbar-btn" disabled={!p.enabled || !p.canRedo} onClick={p.onRedo} title="Redo (Ctrl+Shift+Z)">Redo</button>
    <span className="vtt-topbar-spacer" />{p.children}
  </header>;
}
