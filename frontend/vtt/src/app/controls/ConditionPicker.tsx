import { useEffect, useMemo, useState } from 'react';
import type { GmBoot } from '../../live/boot';
import type { GmEntity } from '../../protocol/frames';
import { conditionChoices,conditionValue,createConditionController,type ConditionChoice,type ConditionController,type ConditionOperation } from '../actions/conditions';
import { actionFeedback } from './feedback';
type Actor = Extract<GmEntity,{kind:'actor'}>;

function ConditionRow({actor,choice,system,enabled,controller}:{actor:Actor;choice:ConditionChoice;system:string;enabled:boolean;controller:ConditionController}) {
  const value = conditionValue(actor,choice.name);
  const [sending,setSending] = useState(false);
  const [waitingFor,setWaitingFor] = useState<number | null>(null);
  const [message,setMessage] = useState('');
  const waiting = waitingFor !== null && waitingFor === value;
  useEffect(() => {
    if (!waiting) {setWaitingFor(null);return;}
    const timer = window.setTimeout(() => {
      setWaitingFor(null);
      setMessage('No update from the tracker yet. Check the live tracker before trying again.');
    },10000);
    return () => window.clearTimeout(timer);
  },[waiting,waitingFor]);
  const disabled = !enabled || sending || waiting;
  async function change(operation:ConditionOperation) {
    if (disabled) return;
    setSending(true); setMessage('');
    const result = await controller.change(actor,choice.name,operation);
    setSending(false);
    if (result.outcome === 'ok') {
      if (result.value === value) {
        setWaitingFor(null);setMessage(`The tracker kept ${choice.label} at ${value}.`);
      } else setWaitingFor(value);
    }
    else setMessage(actionFeedback(result.outcome));
  }
  return <div className="vtt-condition-row">
    <span>{choice.label}{choice.valued && value > 0 ? ` ${value}` : ''}</span>
    {system === 'pf2e' && choice.valued ? <>
      <button type="button" aria-label={`Decrease ${choice.label}`} disabled={disabled || value === 0} onClick={() => void change('decrease')}>−</button>
      <output aria-label={`${choice.label} value`}>{value}</output>
      <button type="button" aria-label={`Increase ${choice.label}`} disabled={disabled} onClick={() => void change('add')}>+</button>
    </> : <button type="button" aria-label={`Toggle ${choice.label}`} aria-pressed={value > 0} disabled={disabled}
      onClick={() => void change(value > 0 ? 'remove' : 'add')}>{value > 0 ? 'On' : 'Off'}</button>}
    {(sending || waiting || message) && <span className="vtt-action-status" role="status">{sending ? 'Sending…' : waiting ? 'Waiting for tracker…' : message}</span>}
  </div>;
}

export function ConditionPicker({boot,actor,enabled}:{boot:GmBoot;actor:Actor;enabled:boolean}) {
  const controller = useMemo(() => createConditionController(boot),[boot.campaignId,boot.csrf]);
  const choices = conditionChoices(boot.system);
  if (!choices.length) return null;
  return <details className="vtt-action-panel"><summary>Conditions</summary>
    {!actor.id.startsWith('cmb_') ? <p>Apply conditions to this character’s encounter entry in the tracker.</p> : choices.map(choice =>
      <ConditionRow key={`${actor.id}:${choice.name}`} actor={actor} choice={choice} system={boot.system} enabled={enabled} controller={controller} />)}
  </details>;
}
