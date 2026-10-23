import { useCallback, useMemo, useState } from 'react';

export function useToolShortcuts(memberId: string | undefined): [boolean, (enabled: boolean) => void] {
  const key = memberId ? `vtt.tool-shortcuts:${memberId}` : null;
  const saved = useMemo(() => {
    try { return !key || localStorage.getItem(key) !== 'false'; }
    catch { return true; }
  }, [key]);
  const [choice, setChoice] = useState<{key: string | null; enabled: boolean} | null>(null);
  const select = useCallback((enabled: boolean) => {
    setChoice({key, enabled});
    try { if (key) localStorage.setItem(key, String(enabled)); }
    catch { /* storage may be disabled */ }
  }, [key]);
  return [choice?.key === key ? choice.enabled : saved, select];
}
