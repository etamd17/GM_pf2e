import type { GmEntity } from '../../protocol/frames';

export function EncounterLine({ actors }: { actors: ReadonlyArray<Extract<GmEntity, { kind: 'actor' }>> }) {
  const combatants = actors.filter((a) => a.id.startsWith('cmb_'));
  if (combatants.length === 0) return <span className="vtt-empty">No encounter running.</span>;
  const turn = combatants.find((a) => a.active);
  return (
    <span className="vtt-empty">
      Encounter: {combatants.length} {combatants.length === 1 ? 'combatant' : 'combatants'}
      {turn ? `, ${turn.name || 'unnamed'}'s turn` : ''}.
    </span>
  );
}

