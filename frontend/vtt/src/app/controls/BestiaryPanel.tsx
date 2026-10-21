import { useEffect, useMemo, useRef, useState } from 'react';
import type { GmBoot } from '../../live/boot';
import type { GmEntity } from '../../protocol/frames';
import { createBestiaryAdder,currentEncounterToken,newRequestId,searchBestiary,type AddedCreature,type BestiaryEntry } from '../actions/bestiary';
import { actionFeedback } from './feedback';
interface Attempt {entry:BestiaryEntry;requestId:string;encounterToken:string}

export function BestiaryPanel({boot,entities,enabled,onPlace}:{boot:GmBoot;entities:readonly GmEntity[];enabled:boolean;onPlace(creature:AddedCreature):boolean}) {
  const adder = useMemo(() => createBestiaryAdder(boot),[boot.campaignId,boot.csrf]);
  const [query,setQuery] = useState('');
  const [results,setResults] = useState<BestiaryEntry[] | null>(null);
  const [searchToken,setSearchToken] = useState<string | null>(null);
  const [searching,setSearching] = useState(false);
  const [adding,setAdding] = useState(false);
  const [attempt,setAttempt] = useState<Attempt | null>(null);
  const [added,setAdded] = useState<AddedCreature | null>(null);
  const [message,setMessage] = useState('');
  const generation = useRef(0);
  const addingRef = useRef(false);
  const latest = useRef({entities,onPlace,enabled});
  latest.current = {entities,onPlace,enabled};
  useEffect(() => () => {generation.current++;},[]);
  if (boot.system !== 'pf2e' && boot.system !== 'cosmere') return null;
  const encounterToken = currentEncounterToken(entities,searchToken);
  async function search() {
    if (query.trim().length < 2 || adding || attempt) return;
    const current = ++generation.current;
    setSearching(true); setResults(null); setMessage('');
    const answer = await searchBestiary(boot,query);
    if (current !== generation.current) return;
    setSearching(false);
    if (answer.outcome !== 'ok') {setMessage(actionFeedback(answer.outcome));return;}
    setResults(answer.results); setSearchToken(answer.encounterToken ?? null);
  }
  async function add(next:Attempt) {
    if (!enabled || addingRef.current) return;
    addingRef.current = true;
    const currentGeneration = generation.current;
    setAdding(true); setMessage('');setAdded(null);
    const answer = await adder.add(next.entry,{encounterToken:next.encounterToken,requestId:next.requestId});
    if (currentGeneration !== generation.current) return;
    setAdding(false);addingRef.current = false;
    if (answer.outcome === 'failed') {
      setAttempt(next);setMessage('The add could not be confirmed. Retry this same attempt, or check the live tracker before starting another.');return;
    }
    setAttempt(null);
    if (answer.outcome !== 'ok' || !answer.creature) {setMessage(actionFeedback(answer.outcome));return;}
    const current = currentEncounterToken(latest.current.entities,searchToken);
    if (current && current !== next.encounterToken) {
      setMessage('The encounter changed after the add. Check the live tracker; no token was placed.');return;
    }
    const placed = latest.current.enabled && latest.current.onPlace(answer.creature);
    if (!placed) setAdded(answer.creature);
    setMessage(placed ? `${answer.creature.name} added; linked token placement queued.` : `${answer.creature.name} added to the tracker. Place its token when the map is ready.`);
    if (answer.incomplete) setMessage(m => `${m} A tracker update needs attention; check the live tracker. Do not add the creature again.`);
  }
  return <section className="vtt-action-panel" aria-label="Bestiary"><h3>Bestiary</h3>
    <form onSubmit={e => {e.preventDefault();void search();}} className="vtt-search-form">
      <label className="vtt-field"><span>Search this campaign’s bestiary</span><input aria-label="Search bestiary" value={query} maxLength={80} disabled={adding || !!attempt}
        onChange={e => {generation.current++;setSearching(false);setQuery(e.target.value);setResults(null);setSearchToken(null);}} /></label>
      <button type="submit" disabled={!enabled || searching || adding || !!attempt || query.trim().length < 2}>Search bestiary</button>
    </form>
    {searching && <p role="status">Searching…</p>}
    {results?.length === 0 && <p>No creatures found.</p>}
    {results && <ul className="vtt-bestiary-results">{results.map(entry => <li key={entry.ref}>
      <span>{entry.name} · {boot.system === 'cosmere' ? 'Tier' : 'Level'} {entry.level}</span>
      <button type="button" disabled={!enabled || adding || !!attempt || !!added || !encounterToken} aria-label={`Add and place ${entry.name}`}
        onClick={() => encounterToken && void add({entry,encounterToken,requestId:newRequestId()})}>Add and place</button>
    </li>)}</ul>}
    {attempt && <div className="vtt-action-buttons">
      <button type="button" disabled={!enabled || adding || encounterToken !== attempt.encounterToken} onClick={() => void add(attempt)}>Retry same add</button>
      <button type="button" disabled={adding} onClick={() => {setAttempt(null);setResults(null);setMessage('Check the live tracker before adding this creature again.');}}>Dismiss add attempt</button>
    </div>}
    {added && <div className="vtt-action-buttons"><button type="button" disabled={!enabled} onClick={() => {if (onPlace(added)) {setAdded(null);setMessage('Linked token placement queued.');}}}>Place added creature</button>
      <button type="button" onClick={() => {setAdded(null);setMessage('Creature kept in the tracker without a token.');}}>Keep in tracker only</button></div>}
    {(message || adding) && <p role="status">{adding ? 'Adding creature…' : message}</p>}
    <p className="vtt-empty">Adds to the live encounter and places a linked token near the scene centre. Undo removes the token; the creature stays in the tracker.</p>
  </section>;
}
