import { useEffect, useRef, type ReactNode } from 'react';

// The UI dialogs keep keyboard focus inside and return it to their opener.
export function ActionDialog({ title, onClose, children }: { title: string; onClose(): void; children: ReactNode }) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    root.current?.querySelector<HTMLElement>('button, a[href], input, select')?.focus();
    return () => { if (opener?.isConnected) opener.focus(); };
  }, []);
  return <div className="vtt-action-backdrop" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
    <div ref={root} role="dialog" aria-modal="true" aria-label={title} className="vtt-action-dialog"
      onKeyDown={e => {
        e.stopPropagation();
        if (e.key === 'Escape') { e.preventDefault(); onClose(); }
        if (e.key !== 'Tab') return;
        const fields = Array.from(root.current?.querySelectorAll<HTMLElement>('button, a[href], input, select') ?? [])
          .filter(el => !el.hasAttribute('disabled'))
          .sort((a,b) => a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1);
        const first = fields[0], last = fields.at(-1);
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
      }}>
      <header><h2>{title}</h2><button type="button" onClick={onClose}>Close</button></header>
      {children}
    </div>
  </div>;
}
