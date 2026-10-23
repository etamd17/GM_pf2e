import type {DragEvent} from 'react';
import type {GmEntity} from '../../protocol/frames';
import {listCreatures} from '../actions/creatures';
interface Props {
  entities:readonly GmEntity[];enabled:boolean;pendingActorIds:ReadonlySet<string>;
  onPlace(actorId:string):void;onDragStart(actorId:string,event:DragEvent<HTMLButtonElement>):void;onDragEnd():void;
}
export function CreaturesPanel(p:Props) {
  const creatures=listCreatures(p.entities);
  return <section aria-label="Creatures"><h2 className="vtt-inspector-title">Creatures</h2>
    <p className="vtt-empty">Drag a Place button onto the map, or activate it to place near the view centre.</p>
    {(['party','encounter'] as const).map(group=>{
      const rows=creatures.filter(c=>c.actorId.startsWith('chr_') === (group === 'party'));
      const heading=group === 'party' ? 'Party' : 'Current encounter';
      return <section key={group} aria-label={heading}><h3>{heading}</h3>
        {!rows.length && <p className="vtt-empty">{group === 'party' ? 'No party characters available.' : 'No creatures in the current encounter.'}</p>}
        <ul className="vtt-creatures">{rows.map(c=>{
          const pending=p.pendingActorIds.has(c.actorId);
          const same=rows.filter(other=>other.name===c.name);
          const name=same.length>1 ? `${c.name} (${same.findIndex(other=>other.actorId===c.actorId)+1})` : c.name;
          return <li key={c.actorId} className="vtt-creature-row"><strong>{name}</strong>
            <span>{c.hp} / {c.maxHp} HP</span><span>Size {c.size} × {c.size} squares</span>
            {c.hiddenInTracker && <span className="vtt-empty">Hidden in tracker; not shown on TV</span>}
            {c.active && <span>Acting now</span>}
            {c.onScene.length>0 && <span>On this map ({c.onScene.length})</span>}
            {pending && <span role="status">Placing…</span>}
            <div className="vtt-creature-actions"><button type="button" aria-label={`Place ${name} (${group})`} disabled={!p.enabled || pending}
              draggable={p.enabled && !pending} title="Drag onto the map, or activate to place near the view centre"
              onClick={()=>p.onPlace(c.actorId)} onDragStart={event=>p.onDragStart(c.actorId,event)} onDragEnd={p.onDragEnd}>Place</button>
              {c.sheetUrl && <a href={c.sheetUrl} target="_blank" rel="noopener noreferrer" aria-label={`Open sheet for ${name}`}>Open sheet</a>}
            </div></li>;
        })}</ul>
      </section>;
    })}
  </section>;
}
