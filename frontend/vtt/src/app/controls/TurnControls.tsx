import { useEffect, useMemo, useState } from 'react';
import type { GmEntity } from '../../protocol/frames';
import type { GmBoot } from '../../live/boot';
import { createTurnController, currentTurn } from '../actions/turns';
import { TrackerLink } from './TrackerLink';
import { actionFeedback } from './feedback';

export function TurnControls({boot,entities,enabled}: {boot:GmBoot;entities:readonly GmEntity[];enabled:boolean}) {
  const controller = useMemo(() => createTurnController(boot), [boot.campaignId,boot.csrf]);
  const turn = currentTurn(entities);
  const [sending,setSending] = useState(false);
  const [waitingFor,setWaitingFor] = useState<string | null>(null);
  const [message,setMessage] = useState('');
  const waiting = waitingFor !== null && turn?.turnToken === waitingFor;
  useEffect(() => {
    if (!waiting) {setWaitingFor(null);return;}
    const timer = window.setTimeout(() => {
      setWaitingFor(null);
      setMessage('No update from the tracker yet. Check the live tracker before trying again.');
    },10000);
    return () => window.clearTimeout(timer);
  },[waiting,waitingFor]);
  if (boot.system !== 'pf2e' && boot.system !== 'cosmere') return null;
  async function change(direction:'next'|'prev') {
    if (!enabled || sending || waiting || !turn) return;
    setSending(true); setMessage('');
    const result = await controller.change(direction,entities);
    setSending(false);
    if (result.outcome === 'ok' || result.outcome === 'stale') setWaitingFor(turn.turnToken);
    if (result.outcome !== 'ok') setMessage(actionFeedback(result.outcome));
  }
  return <section className="vtt-turn-controls" aria-label="Turn controls">
    <span>{turn ? `${turn.name || 'Unnamed'}, round ${turn.round}` : 'No encounter running.'}</span>
    <button type="button" disabled={!enabled || !turn || sending || waiting} onClick={() => void change('prev')}
      title="Previous turn does not undo applied start- or end-of-turn effects">Previous turn</button>
    <button type="button" disabled={!enabled || !turn || sending || waiting} onClick={() => void change('next')}>Next turn</button>
    <TrackerLink boot={boot}/>
    <span role="status">{sending ? 'Sending turn…' : waiting ? (message || 'Waiting for the tracker…') : message}</span>
  </section>;
}
