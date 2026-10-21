import type { ReactNode } from 'react';
import type { GmEntity } from '../../protocol/frames';
interface Summary { id: string; name: string; published: boolean; onTable: boolean }
interface Props {
  scenes: readonly Summary[] | null;
  scene: Extract<GmEntity, { kind: 'scene' }> | null;
  fallbackName: string; enabled: boolean; canUndo: boolean; canRedo: boolean; nextUndo: string | null;
  onOpen(id: string): void; onNew(): void; onPublish(): void; onShow(): void; onClear(): void;
  onUndo(): void; onRedo(): void; children: ReactNode;
}
// Presentation only: navigation and TV commands remain separate callbacks.
export function SceneBar(p: Props) {
  return <header className="vtt-topbar">
    <span className="vtt-brand">VTT</span>
    {p.scenes ? <select className="vtt-scene-picker" aria-label="Scene" value={p.scene?.id ?? ''}
      onChange={(e) => e.target.value && p.onOpen(e.target.value)}>
      {!p.scene && <option value="">No scene</option>}
      {p.scenes.map((s) => <option key={s.id} value={s.id}>{s.name}{s.onTable ? ' (on the table)' : s.published ? '' : ' (prep)'}</option>)}
    </select> : <span className="vtt-scene-name">{p.scene?.name ?? p.fallbackName}</span>}
    <button className="vtt-topbar-btn" disabled={!p.enabled} onClick={p.onNew}>New scene</button>
    {p.scene && <>
      <button className="vtt-topbar-btn" disabled={!p.enabled} onClick={p.onPublish}>{p.scene.published ? 'Unpublish' : 'Publish'}</button>
      <button className="vtt-topbar-btn" disabled={!p.enabled} onClick={p.onShow}>Show on table</button>
      <button className="vtt-topbar-btn" disabled={!p.enabled} onClick={p.onClear}>Clear table</button>
    </>}
    <button className="vtt-topbar-btn" disabled={!p.enabled || !p.canUndo} onClick={p.onUndo}
      title={p.nextUndo ? `Undo ${p.nextUndo} (Ctrl+Z)` : 'Undo (Ctrl+Z)'}>Undo</button>
    <button className="vtt-topbar-btn" disabled={!p.enabled || !p.canRedo} onClick={p.onRedo} title="Redo (Ctrl+Shift+Z)">Redo</button>
    <span className="vtt-topbar-spacer" />{p.children}
  </header>;
}
