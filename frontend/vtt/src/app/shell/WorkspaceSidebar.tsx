import { useCallback, useId, useMemo, useState, type ReactNode } from 'react';
export const SIDEBAR_TABS = ['inspector', 'creatures', 'scene', 'tv', 'import'] as const;
export type SidebarTab = typeof SIDEBAR_TABS[number];
const LABELS: Record<SidebarTab, string> = { inspector: 'Inspector', creatures: 'Creatures', scene: 'Scene', tv: 'TV', import: 'Import' };
export function useSidebarTab(memberId: string | undefined): [SidebarTab, (tab: SidebarTab) => void] {
  const key = memberId ? `vtt.sidebar:${memberId}` : null;
  const saved = useMemo((): SidebarTab => {
    try {
      const value = key ? localStorage.getItem(key) : null;
      return SIDEBAR_TABS.includes(value as SidebarTab) ? value as SidebarTab : 'inspector';
    } catch { return 'inspector'; }
  }, [key]);
  const [choice, setChoice] = useState<{ key: string | null; tab: SidebarTab } | null>(null);
  const select = useCallback((tab: SidebarTab) => {
    setChoice({ key, tab });
    try { if (key) localStorage.setItem(key, tab); } catch { /* storage may be disabled */ }
  }, [key]);
  return [choice?.key === key ? choice.tab : saved, select];
}
interface Props {
  open: boolean; onToggle(): void; tab: SidebarTab; onTab(tab: SidebarTab): void;
  panels: Record<SidebarTab, ReactNode>;
}
export function WorkspaceSidebar({open, onToggle, tab, onTab, panels}: Props) {
  const id = useId();
  return <aside className="vtt-dock" aria-label="Workspace sidebar">
    <button type="button" className="vtt-dock-toggle" aria-expanded={open} onClick={onToggle}>
      {open ? 'Hide sidebar' : 'Show sidebar'}
    </button>
    <div hidden={!open}>
      <div className="vtt-sidebar-tabs" role="tablist" aria-label="Workspace panels">
        {SIDEBAR_TABS.map((name, index) => <button type="button" key={name} role="tab"
          id={`${id}-${name}`} aria-controls={`${id}-${name}-panel`} aria-selected={name === tab}
          tabIndex={name === tab ? 0 : -1} onClick={() => onTab(name)} onKeyDown={(e) => {
            let next: number;
            if (e.key === 'ArrowRight') next = (index + 1) % SIDEBAR_TABS.length;
            else if (e.key === 'ArrowLeft') next = (index + SIDEBAR_TABS.length - 1) % SIDEBAR_TABS.length;
            else if (e.key === 'Home') next = 0;
            else if (e.key === 'End') next = SIDEBAR_TABS.length - 1;
            else return;
            e.preventDefault();
            const target = SIDEBAR_TABS[next]!;
            onTab(target);
            document.getElementById(`${id}-${target}`)?.focus();
          }}>{LABELS[name]}</button>)}
      </div>
      {/* Keep panels mounted: tab switches must not reset pairing, imports or edits. */}
      {SIDEBAR_TABS.map((name) => <section key={name} role="tabpanel" id={`${id}-${name}-panel`}
        aria-labelledby={`${id}-${name}`} hidden={name !== tab}>{panels[name]}</section>)}
    </div>
  </aside>;
}
