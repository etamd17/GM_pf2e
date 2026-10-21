// @vitest-environment jsdom
import { act,cleanup,fireEvent,render,screen,waitFor,within } from '@testing-library/react';
import { afterEach,expect,it,vi } from 'vitest';
import type { GmBoot } from '../../live/boot';
import type { GmEntity } from '../../protocol/frames';
import { TurnControls } from './TurnControls';
import { ConditionPicker } from './ConditionPicker';
import { BestiaryPanel } from './BestiaryPanel';
import { AreaEffectPanel,createAreaAttemptStore } from './AreaEffectPanel';
import { LightingControls,TokenVisionControls } from './LightingControls';
type Of<K extends GmEntity['kind']> = Extract<GmEntity,{kind:K}>;
const boot:GmBoot = {campaignId:'camp_a',sceneId:'scene_a',csrf:'proof',memberId:'gm_a',system:'pf2e'};
const V = 'vAAAAAAAAAAAAAAAAAAAAAAAA';
const actor = (id='cmb_goblin',extra:Partial<Of<'actor'>> = {}) => ({kind:'actor',id,version:V,name:'Goblin',pc:false,hp:20,maxHp:20,conditions:[],hidden:false,active:true,status:null,
  round:2,turnToken:'turn-1',encounterToken:'enc-1',size:1,sheetUrl:null,...extra}) as Of<'actor'>;
const scene = {kind:'scene',id:'scene_a',version:V,name:'Hall',width:640,height:640,gridSize:64,showGrid:true,backgroundAssetId:null,published:true,lighting:false,defaultVision:0} as Of<'scene'>;
const token = {kind:'token',id:'tok_g',version:V,x:96,y:96,size:1,label:'Goblin',color:1,actorId:'cmb_goblin',hidden:false,locked:false,controllerIds:[],artAssetId:null,vision:null} as Of<'token'>;
const template = {kind:'template',id:'tpl_a',version:V,shape:'burst',x:64,y:64,aimX:64,aimY:64,size:3,color:1,hidden:false} as Of<'template'>;
const reply = (value:unknown) => Promise.resolve(new Response(JSON.stringify(value)));
afterEach(() => {cleanup();vi.useRealTimers();vi.unstubAllGlobals();vi.restoreAllMocks();});
const body = (f:ReturnType<typeof vi.fn>,i=0) => JSON.parse(String(f.mock.calls[i]?.[1]?.body));

it('turn buttons send the shown token once, and only the mirror changes the displayed turn', async () => {
  let finish!:(response:Response)=>void;
  const fetcher = vi.fn(() => new Promise<Response>(r => {finish=r;}));vi.stubGlobal('fetch',fetcher);
  const view = render(<TurnControls boot={boot} entities={[actor()]} enabled />);
  fireEvent.click(screen.getByRole('button',{name:'Next turn'}));
  fireEvent.click(screen.getByRole('button',{name:'Next turn'}));
  expect(fetcher).toHaveBeenCalledOnce();expect(body(fetcher).turnToken).toBe('turn-1');
  await act(async () => finish(new Response(JSON.stringify({outcome:'ok',actorId:'cmb_other',round:3,turnToken:'turn-2'}))));
  expect(screen.getByText('Goblin, round 2')).toBeTruthy();
  expect((screen.getByRole('button',{name:'Next turn'}) as HTMLButtonElement).disabled).toBe(true);
  view.rerender(<TurnControls boot={boot} entities={[actor('cmb_other',{name:'Ogre',round:3,turnToken:'turn-2'})]} enabled />);
  expect(screen.getByText('Ogre, round 3')).toBeTruthy();
  expect((screen.getByRole('button',{name:'Previous turn'}) as HTMLButtonElement).disabled).toBe(false);
  expect(screen.getByRole('button',{name:'Previous turn'}).title).toContain('does not undo');
});
it('turn refusals are visible and unsupported or empty encounters cannot be changed', async () => {
  vi.stubGlobal('fetch',vi.fn(() => reply({outcome:'not-live'})));
  const view = render(<TurnControls boot={boot} entities={[actor()]} enabled />);
  fireEvent.click(screen.getByRole('button',{name:'Next turn'}));
  expect(await screen.findByText(/not the live session/)).toBeTruthy();
  view.rerender(<TurnControls boot={boot} entities={[]} enabled />);
  expect((screen.getByRole('button',{name:'Next turn'}) as HTMLButtonElement).disabled).toBe(true);
  view.rerender(<TurnControls boot={{...boot,system:'dnd5e'}} entities={[actor()]} enabled />);
  expect(screen.queryByRole('button',{name:'Next turn'})).toBeNull();
});
it('a stale turn waits for the mirror and never automatically retries', async () => {
  const fetcher=vi.fn(() => reply({outcome:'stale',actorId:'cmb_other',round:3,turnToken:'turn-2'}));vi.stubGlobal('fetch',fetcher);
  const view=render(<TurnControls boot={boot} entities={[actor()]} enabled />);
  fireEvent.click(screen.getByRole('button',{name:'Next turn'}));
  await screen.findByText(/tracker changed/);
  expect((screen.getByRole('button',{name:'Next turn'}) as HTMLButtonElement).disabled).toBe(true);
  view.rerender(<TurnControls boot={boot} entities={[actor('cmb_other',{turnToken:'turn-2',round:3})]} enabled />);
  expect((screen.getByRole('button',{name:'Next turn'}) as HTMLButtonElement).disabled).toBe(false);
  expect(fetcher).toHaveBeenCalledOnce();
});
it('PF2e condition steppers send the mirror value and wait for the mirror to update', async () => {
  const fetcher = vi.fn(() => reply({outcome:'ok',value:3}));vi.stubGlobal('fetch',fetcher);
  const a = actor('cmb_goblin',{conditions:[{name:'frightened',value:2}]});
  const view = render(<ConditionPicker boot={boot} actor={a} enabled />);
  fireEvent.click(screen.getByText('Conditions'));
  fireEvent.click(screen.getByRole('button',{name:'Increase Frightened'}));
  await waitFor(() => expect(body(fetcher)).toMatchObject({actorId:a.id,expectedValue:2,operation:'add'}));
  await act(async () => {});
  expect(screen.getByLabelText('Frightened value').textContent).toBe('2');
  expect((screen.getByRole('button',{name:'Increase Frightened'}) as HTMLButtonElement).disabled).toBe(true);
  view.rerender(<ConditionPicker boot={boot} actor={{...a,conditions:[{name:'frightened',value:3}]}} enabled />);
  expect(screen.getByLabelText('Frightened value').textContent).toBe('3');
  expect((screen.getByRole('button',{name:'Increase Frightened'}) as HTMLButtonElement).disabled).toBe(false);
});
it('condition controls keep the system vocabularies separate and refuse party-only actors', () => {
  const view = render(<ConditionPicker boot={{...boot,system:'cosmere'}} actor={actor()} enabled />);
  fireEvent.click(screen.getByText('Conditions'));
  expect(screen.getByRole('button',{name:'Toggle Determined'})).toBeTruthy();
  expect(screen.queryByRole('button',{name:'Increase Frightened'})).toBeNull();
  expect(screen.queryByRole('button',{name:'Increase Exhausted'})).toBeNull();
  view.rerender(<ConditionPicker boot={boot} actor={actor('chr_party')} enabled />);
  expect(screen.getByText(/encounter entry/)).toBeTruthy();
  expect(screen.queryByRole('button',{name:'Increase Frightened'})).toBeNull();
  view.rerender(<ConditionPicker boot={{...boot,system:'dnd5e'}} actor={actor()} enabled />);
  expect(screen.queryByText('Conditions')).toBeNull();
});
it('condition failures show a message without repainting the condition', async () => {
  vi.stubGlobal('fetch',vi.fn(() => reply({outcome:'stale',value:4})));
  render(<ConditionPicker boot={boot} actor={actor()} enabled />);fireEvent.click(screen.getByText('Conditions'));
  fireEvent.click(screen.getByRole('button',{name:'Toggle Prone'}));
  expect(await screen.findByText(/tracker changed/)).toBeTruthy();
  expect(screen.getByRole('button',{name:'Toggle Prone'}).getAttribute('aria-pressed')).toBe('false');
});

const entry = {ref:'goblin.json',name:'Goblin Warrior',level:1,size:1};
async function searchGoblin() {
  fireEvent.change(screen.getByLabelText('Search bestiary'),{target:{value:'goblin'}});
  fireEvent.click(screen.getByRole('button',{name:'Search bestiary'}));
  await screen.findByRole('button',{name:'Add and place Goblin Warrior'});
}
it('bestiary ignores a late search response after the query changed', async () => {
  const finishes:Array<(r:Response)=>void> = [];
  vi.stubGlobal('fetch',vi.fn(() => new Promise<Response>(r => finishes.push(r))));
  render(<BestiaryPanel boot={boot} entities={[actor()]} enabled onPlace={vi.fn(() => true)} />);
  fireEvent.change(screen.getByLabelText('Search bestiary'),{target:{value:'goblin'}});
  fireEvent.click(screen.getByRole('button',{name:'Search bestiary'}));
  fireEvent.change(screen.getByLabelText('Search bestiary'),{target:{value:'ogre'}});
  fireEvent.click(screen.getByRole('button',{name:'Search bestiary'}));
  await act(async () => finishes[0]!(new Response(JSON.stringify({outcome:'ok',results:[entry],encounterToken:'enc-1'}))));
  expect(screen.queryByText(/Goblin Warrior/)).toBeNull();
  await act(async () => finishes[1]!(new Response(JSON.stringify({outcome:'ok',results:[{...entry,ref:'ogre.json',name:'Ogre'}],encounterToken:'enc-1'}))));
  expect(screen.getByRole('button',{name:'Add and place Ogre'})).toBeTruthy();
});
it('retries a failed bestiary add with the same request id and places only once', async () => {
  let adds=0;
  const fetcher = vi.fn((url:string) => {
    if (url.includes('/bestiary?')) return reply({outcome:'ok',results:[entry],encounterToken:'enc-1'});
    if (++adds===1) return Promise.reject(new TypeError('network'));
    return reply({outcome:'ok',actorId:'cmb_added',name:entry.name,size:1});
  });vi.stubGlobal('fetch',fetcher);
  const place = vi.fn(() => true);render(<BestiaryPanel boot={boot} entities={[actor()]} enabled onPlace={place} />);
  await searchGoblin();fireEvent.click(screen.getByRole('button',{name:'Add and place Goblin Warrior'}));
  await screen.findByRole('button',{name:'Retry same add'});
  const original = body(fetcher,1);fireEvent.click(screen.getByRole('button',{name:'Retry same add'}));
  await waitFor(() => expect(place).toHaveBeenCalledOnce());
  expect(body(fetcher,2)).toEqual(original);
});
it('a successful add with failed token placement retries placement without re-adding', async () => {
  const fetcher=vi.fn((url:string) => url.includes('/bestiary?') ? reply({outcome:'ok',results:[entry],encounterToken:'enc-1'}) :
    reply({outcome:'ok',actorId:'cmb_added',name:entry.name,size:1,incomplete:'broadcast'}));vi.stubGlobal('fetch',fetcher);
  const place=vi.fn().mockReturnValueOnce(false).mockReturnValue(true);
  render(<BestiaryPanel boot={boot} entities={[actor()]} enabled onPlace={place} />);
  await searchGoblin();fireEvent.click(screen.getByRole('button',{name:'Add and place Goblin Warrior'}));
  fireEvent.click(await screen.findByRole('button',{name:'Place added creature'}));
  expect(place).toHaveBeenCalledTimes(2);expect(fetcher).toHaveBeenCalledTimes(2);
});
it('bestiary never places a returned creature after the encounter changed', async () => {
  let finish!:(r:Response)=>void;
  vi.stubGlobal('fetch',vi.fn((url:string) => url.includes('/bestiary?') ? reply({outcome:'ok',results:[entry],encounterToken:'enc-1'}) : new Promise<Response>(r => {finish=r;})));
  const place=vi.fn(() => true);
  const view=render(<BestiaryPanel boot={boot} entities={[actor()]} enabled onPlace={place} />);
  await searchGoblin();fireEvent.click(screen.getByRole('button',{name:'Add and place Goblin Warrior'}));
  view.rerender(<BestiaryPanel boot={boot} entities={[actor('cmb_new',{encounterToken:'enc-2'})]} enabled onPlace={place} />);
  await act(async () => finish(new Response(JSON.stringify({outcome:'ok',actorId:'cmb_added',name:entry.name,size:1}))));
  expect(place).not.toHaveBeenCalled();expect(screen.getByText(/encounter changed after/)).toBeTruthy();
});
it('a late add response after the panel unmounts never places a token', async () => {
  let finish!:(r:Response)=>void;
  vi.stubGlobal('fetch',vi.fn((url:string) => url.includes('/bestiary?') ? reply({outcome:'ok',results:[entry],encounterToken:'enc-1'}) : new Promise<Response>(r => {finish=r;})));
  const place=vi.fn(() => true);
  const view=render(<BestiaryPanel boot={boot} entities={[actor()]} enabled onPlace={place} />);
  await searchGoblin();fireEvent.click(screen.getByRole('button',{name:'Add and place Goblin Warrior'}));
  view.unmount();
  await act(async () => finish(new Response(JSON.stringify({outcome:'ok',actorId:'cmb_added',name:entry.name,size:1}))));
  expect(place).not.toHaveBeenCalled();
});

function areaProps() {return {boot,template,scene,entities:[actor(),actor('cmb_far',{name:'Ogre'}),token],enabled:true,attempts:createAreaAttemptStore()};}
it('area review lets the GM edit proposed targets and resumes with the same request', async () => {
  let requests=0;const fetcher=vi.fn(() => reply(++requests===1 ? {outcome:'ok',incomplete:'stopped',results:[{actorId:'cmb_goblin',outcome:'failed'}]} : {outcome:'ok',results:[]}));
  vi.stubGlobal('fetch',fetcher);render(<AreaEffectPanel {...areaProps()} />);
  expect((screen.getByLabelText('Target Goblin') as HTMLInputElement).checked).toBe(true);
  expect((screen.getByLabelText('Target Ogre') as HTMLInputElement).checked).toBe(false);
  fireEvent.click(screen.getByLabelText('Target Ogre'));
  fireEvent.change(screen.getByLabelText('Area amount'),{target:{value:'12'}});
  fireEvent.click(screen.getByRole('button',{name:'Apply reviewed effect'}));
  const resume = await screen.findByRole('button',{name:'Resume remaining targets'});
  expect(body(fetcher)).toMatchObject({actorIds:['cmb_goblin','cmb_far'],amount:12,damageType:'untyped',encounterToken:'enc-1'});
  fireEvent.click(resume);await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  expect(body(fetcher,1)).toEqual(body(fetcher));
});
it('area targets require a new review after movement, but not after HP-only updates', () => {
  const props=areaProps();const view=render(<AreaEffectPanel {...props} />);
  view.rerender(<AreaEffectPanel {...props} entities={[actor('cmb_goblin',{hp:15}),actor('cmb_far',{name:'Ogre'}),token]} />);
  expect(screen.queryByRole('alert')).toBeNull();
  view.rerender(<AreaEffectPanel {...props} entities={[actor(),actor('cmb_far',{name:'Ogre'}),{...token,x:600,y:600}]} />);
  expect((screen.getByRole('button',{name:'Apply reviewed effect'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button',{name:'Refresh targets'}));
  expect((screen.getByLabelText('Target Goblin') as HTMLInputElement).checked).toBe(false);
});
it('an uncertain save is not offered a retry and another effect requires explicit confirmation', async () => {
  const fetcher=vi.fn(() => reply({outcome:'ok',uncertain:'emitter',results:[{actorId:'cmb_goblin',outcome:'uncertain'}]}));vi.stubGlobal('fetch',fetcher);
  vi.stubGlobal('confirm',vi.fn(() => false));render(<AreaEffectPanel {...areaProps()} />);
  fireEvent.change(screen.getByLabelText('Area action'),{target:{value:'save'}});
  fireEvent.click(screen.getByRole('button',{name:'Apply reviewed effect'}));
  await screen.findByText(/may have reached some players/);
  expect(screen.queryByRole('button',{name:'Retry same effect'})).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'Start another effect'}));
  expect((screen.getByRole('button',{name:'Apply reviewed effect'}) as HTMLButtonElement).disabled).toBe(true);
  expect(fetcher).toHaveBeenCalledOnce();
});
it('area vocabularies stay separate and reject invalid amounts', () => {
  const view=render(<AreaEffectPanel {...areaProps()} boot={{...boot,system:'cosmere'}} />);
  expect(within(screen.getByLabelText('Area damage type')).queryByRole('option',{name:'fire'})).toBeNull();
  expect(screen.queryByRole('option',{name:'Request saves'})).toBeNull();
  fireEvent.change(screen.getByLabelText('Area amount'),{target:{value:'0'}});
  expect((screen.getByRole('button',{name:'Apply reviewed effect'}) as HTMLButtonElement).disabled).toBe(true);
  view.rerender(<AreaEffectPanel {...areaProps()} boot={{...boot,system:'dnd5e'}} />);
  expect(screen.queryByRole('region',{name:'Area effect review'})).toBeNull();
});
it('identically named area targets can be distinguished by their map positions', () => {
  render(<AreaEffectPanel {...areaProps()} entities={[actor(),actor('cmb_another'),token]} />);
  expect(screen.getByRole('checkbox',{name:'Target Goblin (1) — square 2, 2'})).toBeTruthy();
  expect(screen.getByRole('checkbox',{name:'Target Goblin (2) — not on this map'})).toBeTruthy();
});
it('lighting uses the published conversion and keeps a focused draft through live scene updates', () => {
  const issue=vi.fn(),preview=vi.fn();const view=render(<LightingControls scene={scene} enabled preview={false} onPreview={preview} onIssue={issue} />);
  const input=screen.getByLabelText('Default party vision (ft)');fireEvent.focus(input);fireEvent.change(input,{target:{value:'45'}});
  view.rerender(<LightingControls scene={{...scene,version:'vBBBBBBBBBBBBBBBBBBBBBBBB',defaultVision:128}} enabled preview={false} onPreview={preview} onIssue={issue} />);
  expect((input as HTMLInputElement).value).toBe('45');fireEvent.blur(input);
  expect(issue).toHaveBeenCalledWith(expect.objectContaining({type:'scene.update',sceneId:scene.id,
    expected:[{kind:'scene',id:scene.id,version:'vBBBBBBBBBBBBBBBBBBBBBBBB'}],payload:{lighting:false,defaultVision:576}}));
});
it('GM vision preview is local, while token overrides use the current token version', () => {
  const issue=vi.fn(),preview=vi.fn();const view=render(<LightingControls scene={{...scene,lighting:true}} enabled preview={false} onPreview={preview} onIssue={issue} />);
  fireEvent.click(screen.getByLabelText('Preview party vision on this GM screen'));
  expect(preview).toHaveBeenCalledWith(true);expect(issue).not.toHaveBeenCalled();
  view.unmount();render(<TokenVisionControls token={{...token,vision:128}} scene={scene} enabled onIssue={issue} />);
  fireEvent.click(screen.getByLabelText('Use scene vision'));
  expect(issue).toHaveBeenCalledWith(expect.objectContaining({type:'token.update',expected:[{kind:'token',id:token.id,version:V}],payload:{id:token.id,vision:null}}));
});


it('an unchanged successful condition re-enables the row without painting the response', async () => {
  vi.stubGlobal('fetch',vi.fn(() => reply({outcome:'ok',value:2})));
  render(<ConditionPicker boot={boot} actor={actor('cmb_goblin',{conditions:[{name:'frightened',value:2}]})} enabled />);
  fireEvent.click(screen.getByText('Conditions'));
  fireEvent.click(screen.getByRole('button',{name:'Increase Frightened'}));
  await screen.findByText('The tracker kept Frightened at 2.');
  expect((screen.getByRole('button',{name:'Increase Frightened'}) as HTMLButtonElement).disabled).toBe(false);
  expect(screen.getByLabelText('Frightened value').textContent).toBe('2');
});

it.each(['turn','condition'] as const)('%s mirror wait expires without retrying or painting an answer', async control => {
  const fetcher=vi.fn(() => reply(control === 'turn' ? {outcome:'ok',turnToken:'turn-2'} : {outcome:'ok',value:1}));
  vi.stubGlobal('fetch',fetcher);
  if (control === 'turn') render(<TurnControls boot={boot} entities={[actor()]} enabled />);
  else {render(<ConditionPicker boot={boot} actor={actor()} enabled />);fireEvent.click(screen.getByText('Conditions'));}
  const button=screen.getByRole('button',{name:control === 'turn' ? 'Next turn' : 'Toggle Prone'}) as HTMLButtonElement;
  vi.useFakeTimers({toFake:['setTimeout','clearTimeout']});
  await act(async () => {fireEvent.click(button);await Promise.resolve();await Promise.resolve();});
  // Flush the real Response body before advancing only the mirror deadline.
  await act(async () => {await new Promise(resolve => setImmediate(resolve));});
  expect(button.disabled).toBe(true);
  await act(async () => {vi.advanceTimersByTime(10000);});
  expect(button.disabled).toBe(false);
  expect(screen.getByText(/No update from the tracker yet/)).toBeTruthy();
  expect(fetcher).toHaveBeenCalledOnce();
  if (control === 'turn') expect(screen.getByText('Goblin, round 2')).toBeTruthy();
  else expect(button.getAttribute('aria-pressed')).toBe('false');
});

it('a deselected in-flight area attempt receives its result and retries the original request on reselect', async () => {
  let finish!:(r:Response)=>void;
  const fetcher=vi.fn(() => new Promise<Response>(r => {finish=r;}));vi.stubGlobal('fetch',fetcher);
  const props=areaProps();const view=render(<AreaEffectPanel {...props} />);
  fireEvent.change(screen.getByLabelText('Area amount'),{target:{value:'12'}});
  fireEvent.click(screen.getByRole('button',{name:'Apply reviewed effect'}));
  const original=body(fetcher);
  view.rerender(<div />);
  view.rerender(<AreaEffectPanel {...props} />);
  expect(screen.getByText('Applying effect…')).toBeTruthy();
  expect((screen.getByRole('button',{name:'Apply reviewed effect'}) as HTMLButtonElement).disabled).toBe(true);
  // Response arrives while deselected and remains in the workspace-owned store.
  view.rerender(<div />);
  await act(async () => finish(new Response(JSON.stringify({outcome:'failed'}))));
  view.rerender(<AreaEffectPanel {...props} />);
  expect((screen.getByLabelText('Area amount') as HTMLInputElement).value).toBe('12');
  fireEvent.click(screen.getByRole('button',{name:'Retry same effect'}));
  expect(body(fetcher,1)).toEqual(original);
});

it.each(['failed','uncertain'] as const)('a completed %s area attempt survives deselection and requires confirmation for a new effect', async outcome => {
  const fetcher=vi.fn(() => reply(outcome === 'failed' ? {outcome:'failed'} : {outcome:'ok',uncertain:'emitter'}));
  vi.stubGlobal('fetch',fetcher);const confirm=vi.fn(() => false);vi.stubGlobal('confirm',confirm);
  const props=areaProps();const view=render(<AreaEffectPanel {...props} />);
  if (outcome === 'uncertain') fireEvent.change(screen.getByLabelText('Area action'),{target:{value:'save'}});
  fireEvent.click(screen.getByRole('button',{name:'Apply reviewed effect'}));
  if (outcome === 'failed') await screen.findByRole('button',{name:'Retry same effect'});
  else await screen.findByText(/may have reached some players/);
  view.rerender(<div />);
  // A different template does not inherit the first one's attempt.
  view.rerender(<AreaEffectPanel {...props} template={{...template,id:'tpl_other'}} />);
  expect((screen.getByRole('button',{name:'Apply reviewed effect'}) as HTMLButtonElement).disabled).toBe(false);
  view.rerender(<div />);
  view.rerender(<AreaEffectPanel {...props} />);
  expect((screen.getByRole('button',{name:'Apply reviewed effect'}) as HTMLButtonElement).disabled).toBe(true);
  if (outcome === 'uncertain') {
    expect(screen.getByText(/may have reached some players/)).toBeTruthy();
    expect(screen.queryByRole('button',{name:'Retry same effect'})).toBeNull();
    expect((screen.getByLabelText('Area action') as HTMLSelectElement).value).toBe('save');
  }
  fireEvent.click(screen.getByRole('button',{name:'Start another effect'}));
  expect(confirm).toHaveBeenCalledOnce();expect(fetcher).toHaveBeenCalledOnce();
  expect((screen.getByRole('button',{name:'Apply reviewed effect'}) as HTMLButtonElement).disabled).toBe(true);
});
