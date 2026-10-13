# Task queue

One shared list for Claude and Codex. Protocol: `docs/handoff/PROTOCOL.md`.

- **Status:** `needs brief` / `ready` / `in-progress` / `review` / `done` / `waiting on user`.
- **Owner** is the default tool; either may take a `ready` task. A task is `ready` only once
  its brief exists in `tasks/`.
- **Model column** = suggested setting for that step (high effort only where mistakes are costly).
- When you finish a task: write the report in its task file, update its row here, and add a
  plain one-line entry to **Log** at the bottom.

## D&D 5e

| ID | Task | Owner | Model | Status | Brief |
|---|---|---|---|---|---|
| 5E-04 | Species versions as a builder choice (Draconic Ancestry, 2024 lineages) + fix raw text in sheet resistances | codex | standard | ready | `tasks/5E-04.md` |
| 5E-06 | Delete combatant pictures nothing uses | codex | standard | ready | `tasks/5E-06.md` |
| 5E-03 | Database ownership for 5e characters (only needed before the site uses PostgreSQL for the web app) | claude | Opus, high | needs brief | - |
| 5E-05 | Private drafts for 5e characters | claude | Opus, high | needs brief (after 5E-03) | - |

Done: 5E-00 data stays out of git; 5E-01 merged as PR #175; 5E-02 data installed on Railway,
checked live, and survived a redeploy (2026-10-13).

## UI/UX (PF2e + Cosmere)

Approved directions for 18 screens are recorded in `tasks/UI-00.md` (Codex's design review; the
mockups are in the local-only bundle `assets/UI-00-review.zip`). Nothing has been built yet.
UI/UX design is Claude's by default; Codex implements mechanical parts from Claude's briefs.

| ID | Task | Owner | Model | Status | Brief |
|---|---|---|---|---|---|
| UI-00 | Design review checkpoint: 18 screens approved as mockups | codex | - | done (review: accepted, see below) | `tasks/UI-00.md` |
| UI-01 | Finish the design review: Cosmere generators, then Cosmere sheet, level-up and builder. Mockups first; the user picks a direction for each | claude | Opus, medium-high | ready | `tasks/UI-00.md` > What's next |
| UI-02 | Build the approved screens, one small task per screen, starting from current `main` (the mockups were made on an older snapshot) | claude briefs, codex builds | briefs: Opus high; build: standard | needs brief | - |
| UI-03 | Fix the real bugs the review found. Check each on current `main` first, then fix it with a test | codex | standard | needs brief | - |

UI-03 bug list (from `tasks/UI-00.md` > Anything broken or risky):

| Bug | Checked on current `main`? |
|---|---|
| PF2e GM screen: the polish script looks for `.masonry`, which no longer exists, so it never runs | **yes, real** |
| Map: with no scene, an error message can crash before the message system is set up | **yes, real (read in the code)** |
| PF2e builder: ancestry feat treated as text in the review step | not yet |
| PF2e level-up: the gains panel can block clicks on the picker | not yet |
| Cosmere loot: totals ignore gems; deleting a log doesn't undo the credit; failed saves can clear the form | not yet |
| Cosmere roster/vitals: no live updates; old maximums | not yet |
| Cosmere tracker shows PF2e-only controls (rules question for the user, not a plain bug) | not yet |
| Campaign delete: the wording says "delete" but it's moved to the trash | not yet |

## Cosmere: Mistborn

| ID | Task | Owner | Model | Status | Brief |
|---|---|---|---|---|---|
| MB-00 | Codex's handoff for the Mistborn work (in progress: still reading the Mistborn materials) | codex | its strongest | in-progress | `tasks/MB-00.md` (not written yet) |

Once MB-00 is in, Claude reads it, checks it against the code and splits the rest into tasks here.

## UI-00 review (Claude, 2026-10-13)

Accepted as a design checkpoint. Notes:
- The commit that added it (`312138ed`) is on `remediation/pr6b3e1-legal-readiness`, whose PR
  (#174) is already merged, so it isn't headed to `main`. This file now lives here instead.
- The 22 MB mockup bundle should not go into git (public repo, large). `docs/handoff/assets/`
  is now gitignored; keep bundles local.
- Two of the bugs were checked on current `main` (marked above); the rest are unconfirmed.

## Log

Newest first. One plain line per handoff: who, what, what's next.

- 2026-10-13 Claude: merged UI-00 into this queue; added the UI/UX and Mistborn sections. Next: UI-01 (Claude), 5E-04/5E-06 (Codex), MB-00 (Codex).
- 2026-10-13 Codex: wrote UI-00, the design review checkpoint (18 screens approved as mockups).
- 2026-10-13 Claude: 5e merged (PR #175), installed on Railway and checked live.
