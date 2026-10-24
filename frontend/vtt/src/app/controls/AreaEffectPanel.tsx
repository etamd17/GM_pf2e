import { useMemo, useState, useSyncExternalStore } from 'react';
import type { GmBoot } from '../../live/boot';
import type { GmEntity } from '../../protocol/frames';
import { currentEncounterToken,newRequestId } from '../actions/bestiary';
import { createAreaApplier,damageTypes,savesFor,templateTargets,type AreaRequest } from '../actions/areaEffects';
import { createAreaAttemptStore,type Attempt } from './areaAttemptStore';
export { createAreaAttemptStore } from './areaAttemptStore';
import { actionFeedback } from './feedback';
type Of<K extends GmEntity['kind']> = Extract<GmEntity,{kind:K}>;
interface Review {signature:string;selected:string[];names:Record<string,string>}
export function AreaEffectPanel({boot,template,scene,entities,enabled,attempts}:{attempts:ReturnType<typeof createAreaAttemptStore>;boot:GmBoot;template:Of<'template'>;scene:Of<'scene'>;entities:readonly GmEntity[];enabled:boolean}) {
  const areas = useMemo(() => createAreaApplier(boot),[boot.campaignId,boot.csrf]);
  const attemptKey = `${scene.id}:${template.id}`;
  const held = useSyncExternalStore(attempts.subscribe,() => attempts.get(attemptKey));
  const attempt = held?.request ?? null;
  const answer = held?.answer ?? null;
  const sending = held?.sending ?? false;
  const actors = entities.filter((e):e is Of<'actor'> => e.kind === 'actor' && e.id.startsWith('cmb_'));
  const tokens = entities.filter((e):e is Of<'token'> => e.kind === 'token');
  const encounterToken = currentEncounterToken(entities);
  const signature = JSON.stringify([scene.id,scene.gridSize,template.version,encounterToken,
    actors.map(a => a.id).sort(),tokens.map(t => [t.id,t.x,t.y,t.size,t.actorId]).sort()]);
  const targetName = (a:Of<'actor'>) => {
    const same = actors.filter(other => other.name === a.name);
    if (same.length < 2) return a.name;
    const placed = tokens.find(t => t.actorId === a.id);
    return `${a.name} (${same.findIndex(other => other.id === a.id) + 1})${placed ?
      ` — square ${Math.floor(placed.x / scene.gridSize) + 1}, ${Math.floor(placed.y / scene.gridSize) + 1}` : ' — not on this map'}`;
  };
  const propose = ():Review => ({signature,names:Object.fromEntries(actors.map(a => [a.id,a.name])),selected:[...new Set(templateTargets(template,tokens,scene.gridSize)
    .map(t => t.actorId).filter((id):id is string => !!id && actors.some(a => a.id === id)))]});
  const [review,setReview] = useState(propose);
  const [action,setAction] = useState<'damage'|'heal'|'save'>(attempt?.action ?? 'damage');
  const [amount,setAmount] = useState(attempt && 'amount' in attempt ? String(attempt.amount) : '1');
  const [damageType,setDamageType] = useState(attempt && 'damageType' in attempt ? attempt.damageType : damageTypes(boot.system)[0] ?? '');
  const [save,setSave] = useState<string>(attempt && 'save' in attempt ? attempt.save : savesFor(boot.system)[0] ?? '');
  const [dc,setDc] = useState(attempt && 'dc' in attempt ? String(attempt.dc) : '15');
  if (!damageTypes(boot.system).length) return null;
  const changed = signature !== review.signature;
  const n = Number(action === 'save' ? dc : amount);
  const validNumber = (action === 'save' ? dc : amount).trim() !== '' && Number.isInteger(n) && n >= 1 && n <= (action === 'save' ? 60 : 100000);
  const canApply = enabled && !sending && !changed && !!encounterToken && review.selected.length > 0 && validNumber;
  async function send(request:Attempt) {
    if (!enabled || attempts.get(attemptKey)?.sending) return;
    const names = held?.names ?? review.names;
    attempts.set(attemptKey,{request,answer:null,sending:true,names});
    const result = await areas.apply(request);
    attempts.set(attemptKey,{request,answer:result,sending:false,names});
  }
  function apply() {
    if (!canApply || attempt) return;
    const request:AreaRequest = action === 'save' ? {action,actorIds:review.selected,save,dc:n} :
      action === 'heal' ? {action,actorIds:review.selected,amount:n} : {action,actorIds:review.selected,amount:n,damageType};
    void send({...request,encounterToken,requestId:newRequestId()});
  }
  return <section className="vtt-action-panel" aria-label="Area effect review"><h3>Review area effect</h3>
    <p>The template proposes targets. Confirm the creatures and values before applying.</p>
    {changed && <p role="alert">The scene, template or encounter changed. Refresh targets before applying.</p>}
    <button type="button" disabled={sending} onClick={() => {
      if (attempt && (answer?.outcome === 'failed' || answer?.incomplete || answer?.uncertain) &&
        !window.confirm('Some effects may already have applied. Check the tracker and players before starting another effect. Start a new review?')) return;
      setReview(propose());attempts.set(attemptKey,null);
    }}>{attempt ? 'Start another effect' : 'Refresh targets'}</button>
    <fieldset disabled={!enabled || sending || !!attempt}><legend>Targets</legend>
      {actors.length === 0 && <p>No linked encounter creatures available.</p>}
      {actors.map(a => <label key={a.id} className="vtt-field vtt-field--toggle"><input type="checkbox" aria-label={`Target ${targetName(a)}`} checked={(attempt?.actorIds ?? review.selected).includes(a.id)}
        onChange={e => setReview(r => ({...r,selected:e.target.checked ? [...r.selected,a.id] : r.selected.filter(id => id !== a.id)}))} />{targetName(a)}</label>)}
    </fieldset>
    <fieldset disabled={!enabled || sending || !!attempt}><legend>Effect</legend>
      <label className="vtt-field">Action<select aria-label="Area action" value={action} onChange={e => setAction(e.target.value as typeof action)}>
        <option value="damage">Damage</option><option value="heal">Heal</option>{savesFor(boot.system).length > 0 && <option value="save">Request saves</option>}</select></label>
      {action === 'save' ? <>
        <label className="vtt-field">Save<select aria-label="Area save" value={save} onChange={e => setSave(e.target.value)}>{savesFor(boot.system).map(s => <option key={s}>{s}</option>)}</select></label>
        <label className="vtt-field">DC<input aria-label="Area DC" type="number" min={1} max={60} step={1} value={dc} onChange={e => setDc(e.target.value)} /></label>
        <p className="vtt-empty">PCs receive a save request. Roll for other creatures in the tracker; damage is a separate reviewed action.</p>
      </> : <label className="vtt-field">Amount<input aria-label="Area amount" type="number" min={1} max={100000} step={1} value={amount} onChange={e => setAmount(e.target.value)} /></label>}
      {action === 'damage' && <label className="vtt-field">Damage type<select aria-label="Area damage type" value={damageType} onChange={e => setDamageType(e.target.value)}>{damageTypes(boot.system).map(t => <option key={t}>{t}</option>)}</select></label>}
    </fieldset>
    <button type="button" disabled={!canApply || !!attempt} onClick={apply}>Apply reviewed effect</button>
    {attempt && answer && !answer.uncertain && (answer.outcome === 'failed' || answer.incomplete) &&
      <button type="button" disabled={!enabled || sending || encounterToken !== attempt.encounterToken} onClick={() => void send(attempt)}>
        {answer.incomplete ? 'Resume remaining targets' : 'Retry same effect'}</button>}
    {sending && <p role="status">Applying effect…</p>}
    {answer && <div role="status"><p>{answer.outcome === 'ok' ? 'Effect processed. Changes arrive from the tracker.' : actionFeedback(answer.outcome)}</p>
      {answer.incomplete && <p>Only part of the group was processed. Check failed creatures in the tracker; resume sends only unfinished targets.</p>}
      {answer.uncertain && <p>A save request may have reached some players. Ask them before sending another; this attempt will not be sent again.</p>}
      {answer.results && <ul>{answer.results.map(r => <li key={r.actorId}>{(held?.names ?? review.names)[r.actorId] ?? 'Creature'}: {
        ({applied:'Applied',failed:'Failed; check the tracker',gone:'No longer in the encounter',requested:'Save requested','gm-rolls':'GM rolls in the tracker',uncertain:'Delivery uncertain; ask the player'})[r.outcome]
      }{r.defeated ? ' — defeated' : ''}</li>)}</ul>}
    </div>}
  </section>;
}
