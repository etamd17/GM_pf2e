# UI-09: PF2e party overview, campaign management (B) and My table (A)

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** codex (standard model, high effort: account pages touch roles and invites)
- **Branch:** `codex/ui-09-party-campaign-pages` from current `origin/main`
- **Depends on:** none
- **Context:** **read "Conventions for every PF2e screen" in `tasks/UI-06-pf2e-gm-hub-and-gmscreen.md`
  first.** Decision log entries: "PF2e party overview", "PF2e campaign management", "Campaign
  management selection", "Campaign preview verification", "Campaign source follow-ups".

## Goal
- **Party overview, combined** (`templates/party_view.html`, mockups `pf2e-party-combined.html`,
  `pf2e-party-revisions.css`, `pf2e-party-recompose.js`): Revision A's character cards with the
  attributes always visible, plus Revision B's clickable attention strip (who is low on HP, dying,
  has conditions; clicking jumps to that card). Keep the character tabs and every party control.
- **Campaign management, B** (`templates/campaign_invites.html`, mockup
  `pf2e-campaign-manage-session.html`): three tabs (Members & invites / Characters / Settings), with
  counts and arrow-key navigation. All 12 forms keep their action, method and fields; the
  last-GM conditional and empty-roster states still render. Keep the UI-03 trash wording.
- **My table, A** (`templates/account_home.html`, mockup `pf2e-campaign-table-refine.html`):
  campaigns and characters visible together as compact cards. GM view keeps its 8 forms, player
  view its 5; the player view still has no GM management or backup links.

## Constraints
- Don't add features the mockups only described: no character hand-off for PF2e, no ownership
  reassignment, no co-GM role. Remove any PF2e hand-off wording you find, since PF2e has no
  such control.
- Opening the invites page can mint claim links (decision log), so browser checks use a
  throwaway campaign only.
- Tabs must work without JavaScript errors when a section is empty, and the URL hash should open the
  matching tab (so the existing anchors such as `#members` still land correctly).

## Acceptance
- [ ] Each page matches its chosen mockup at 1440px and 375px.
- [ ] Inventory tests: form actions/methods/field names identical (12 manage, 8 GM My table, 5
      player My table); player view has no GM-only links (role test).
- [ ] Attention strip: a dying/low-HP PC appears in it and links to its card (test with a fixture).
- [ ] Existing account/campaign tests plus the convention gates pass (numbers).

## Out of scope
- Role/permission changes, backups, invite logic, the Cosmere roster (Cosmere batch).

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

## Review
