import type { AreaRequest,AreaResult } from '../actions/areaEffects';
export type Attempt = AreaRequest & {encounterToken:string | null;requestId:string};
interface AttemptState {request:Attempt;answer:AreaResult | null;sending:boolean;names:Record<string,string>}

// Kept outside the lazy panel: deselection must not forget an uncertain write.
export function createAreaAttemptStore() {
  const records = new Map<string,AttemptState>();
  const listeners = new Set<() => void>();
  return {
    get:(key:string) => records.get(key) ?? null,
    set(key:string,value:AttemptState | null) {
      if (value) records.set(key,value); else records.delete(key);
      listeners.forEach(notify => notify());
    },
    subscribe(notify:() => void) {listeners.add(notify);return () => {listeners.delete(notify);};},
  };
}
