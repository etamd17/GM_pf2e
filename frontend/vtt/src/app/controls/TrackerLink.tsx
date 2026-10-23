import type {GmBoot} from '../../live/boot';
// The tracker has one live campaign slot. Activation owns the authorization,
// save/switch and takeover confirmation; a direct /tracker link cannot bind it.
export function TrackerLink({boot}:{boot:Pick<GmBoot,'campaignId'|'csrf'>}) {
  return <form className="vtt-tracker-form" method="post" action={`/campaign/${encodeURIComponent(boot.campaignId)}/activate`} target="_blank" rel="noopener">
    <input type="hidden" name="_csrf" value={boot.csrf}/><input type="hidden" name="then" value="/tracker"/>
    <button type="submit" className="vtt-link-button" title="Opens this campaign’s tracker in a new tab; another GM’s live session requires confirmation">Open tracker</button>
  </form>;
}
