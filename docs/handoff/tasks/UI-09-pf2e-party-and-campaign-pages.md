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

- Ported the approved combined party view: Revision A's flatter cards keep attributes visible, while a server-rendered Party watch links low-HP and conditioned PCs to stable card anchors. Live HP and condition updates add, reorder and remove watch entries, announce changes politely, and move keyboard focus into the selected card without changing any PF2e controls. Boolean conditions render as labels without exposing their stored `True` value.
- Rebuilt campaign management as three server-rendered tabs for Members & invites, Characters and Settings. Counts, existing anchors, hash navigation, roving arrow/Home/End keyboard focus, empty states, all 12 form contracts and the last-GM protection remain. System and delete responses return to the Settings subsection that contains their notice, and the existing Cosmere-only release returns to Characters. PF2e hand-off wording is gone. Confirmation prompts now safely encode campaign, member and character names.
- Applied the selected compact My table styling without reordering its sections or changing behavior. The GM render keeps eight forms and management/backup links; the player render keeps five forms and has no GM-only links.
- Kept system mechanics isolated: `/party` redirects Cosmere campaigns to their roster before loading PF2e data, while the existing D&D redirect continues to handle D&D campaigns.
- Added `tests/test_pf2e_party_campaign_ui.py` with frozen rendered inventories for IDs, links and targets, every inline handler, full form/control contracts, ARIA, JS selector classes and data hooks, plus the live Party watch, campaign tabs, hostile-name escaping and cross-system route guards.

### Verification

- `python tools/check_templates.py` — **107 templates parse**.
- `pytest -q tests/test_pf2e_party_campaign_ui.py tests/test_registration_and_members.py tests/test_onboarding.py tests/test_campaign_soft_delete.py tests/test_campaign_backup.py tests/test_scheduled_backups.py tests/test_switching_ux.py tests/test_switching_e2e.py tests/test_gm_system_repair.py tests/test_nav_home_and_session_entry.py tests/test_system_guards.py tests/test_inline_handler_escaping.py tests/test_route_policy_inventory.py` — **82 passed**.
- Final correction reruns: `pytest -q tests/test_pf2e_party_campaign_ui.py` — **12 passed**; `pytest -q tests/test_gm_system_repair.py tests/test_campaign_soft_delete.py` — **10 passed**.
- `git diff --check` — **passed**.
- Claude browser review remains: use a throwaway campaign for the management page, then check all three pages at 1440px and 375px with no horizontal overflow.

## Review

**Claude, 2026-10-15: accepted. No required fixes.**

Read the diff (`app.py` redirects and anchors, the three templates, the new tests). Checked live
on a local server on this tree at 1440px and 375px. Used a throwaway campaign, "UI09 throwaway",
for the destructive actions. Did not rerun the test suite.

**Party (PF2e, Kyle in a live encounter):**
- At rest the Party watch is empty.
- After 100 damage plus Prone and Frightened through the tracker routes, it updated without a
  reload to "Kyle · 38 / 138 HP, Frightened 1, Prone" (no "Prone True").
- Clicking the entry focuses Kyle's card (`#party-card-1`, in view).
- Healing him and removing both conditions cleared the entry live.
- No sideways scroll at 1440 or 375.
- Not tested live: ordering, since the test party has one character. It's covered by Codex's
  tests.

**Campaign management (throwaway campaign):**
- Three tabs (Members & invites / Characters / Settings). Mouse, ArrowRight, Home and End all
  work, and the URL hash follows (`#characters`).
- Loading the page with `#characters` opens that tab.
- Empty states read "No characters are available for this campaign yet".
- A wrong-name delete returns to `?delete_error=1#danger`, on the Settings tab, with "The name
  didn't match -- the campaign was not moved to trash." visible.
- Changing the system returns to `?system_set=pf2e#system`, with "Game system set to PF2E." in
  view.
- The empty campaign renders its 4 forms (invite x2, system, delete); the 12-form inventory is
  covered by Codex's tests on a populated fixture.
- No sideways scroll at 375px.
- Not clicked: Cosmere "Hand off". It's a one-line redirect-anchor change (`#characters`), checked
  in the code.

**My Table:**
- GM view: no sideways scroll at 375px.
- The template change is CSS plus an `account-home` body class, with no change to any `{% if %}`
  role branch, so the player view's separation is unchanged by construction. I couldn't view it as
  a player (no player account in this test setup); Codex's role tests cover it.

**System isolation:** `/party` in a Cosmere campaign redirects to `/cosmere/pcs` (0.7 s); in PF2e
it renders the PF2e Party View.
