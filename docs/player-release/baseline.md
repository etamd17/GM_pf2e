# Stage 0 evidence baseline

Observed 2026-11-08. This is source/configuration review, not a gameplay or full
test certification. Historical test numbers in source handoffs were not rerun.

## Repository and branch evidence

- Remote main was read through the GitHub connector at
  `https://github.com/etamd17/GM_pf2e/commits/main` and matched local `origin/main`:
  `7aeb9549c1df2807935a4a44229284e13a5add1d`, the PR #242 merge on 2026-11-02.
- DS-03 is therefore merged even though the committed queue calls it a CI blocker.
- The current open-PR listing showed dependency updates #241, #223, #217, #176
  and #156. No player-feature PR appeared in that returned listing. Local
  uncommitted/branch-only work remains important and is not inferred from PRs.
- The original `GM_pf2e` checkout is `remediation/pr4b1-authorization-shadow`,
  `cbefabe7`, 253 commits behind main, with tracked and untracked authorization
  changes. It was not altered. The Stage 0 package uses a new sparse docs worktree.
- [Player work audit](existing-player-work.md) records UI-11/UI-18 pending patches,
  merged D&D work, design foundations and rules-branch disposition.
- [Obsidian/VTT audit](existing-obsidian-vtt-work.md) records committed recovery
  work missing from main, current VTT handoffs and wiki planning files.

Source review commands included `git worktree list --porcelain`, per-worktree
`git status --short --branch`, `git rev-list --left-right --count`, `git cherry`,
`git merge-base --is-ancestor`, `git diff --stat`, and scoped `git show`/`rg` reads.
No resets, cleanup, rebases, source-branch merges or source-worktree edits occurred.

## Production configuration observations

Read-only Railway connector inspection selected project `gamemaster-tool`,
environment `production`, service `GM_pf2e` by returned names/IDs. Observed:

| Item | Evidence / limit |
|---|---|
| Source | `etamd17/GM_pf2e`, branch `main`; configured source is not by itself a deployed-SHA attestation. |
| Latest deployment | `498600ce-e4be-43c0-aee3-e8b3c6c044d7`, `SUCCESS`, 2026-11-02T23:35:35Z. |
| Replicas | One configured production replica. |
| Storage | One 5 GB volume mounted at `/data`. This is configuration evidence; restore durability was not rehearsed. |
| Builder | Service configuration reports RAILPACK/V3. Older source guidance describes Nixpacks; reconcile actual build logs/config before any deployment change. |
| SQL availability | Production inventory contains only `GM_pf2e`, not a Postgres service. Project-level Postgres and `vtt-web` names exist, with a separate `vtt-staging` environment; do not assume those are a production game-state backend. |
| Backend flags | Reported service/shared variable names contain no `OWNERSHIP_BACKEND`, `STATE_BACKEND` or `DATABASE_URL`. Values were not read. Source defaults point to JSON/file behavior, but runtime authority was not directly attested. |
| Worker command | Committed `Procfile` uses one gevent worker. Actual process command was not inspected. |
| Health | Read-only public `/health` request eventually returned `{ "status": "healthy" }`. The initial sandbox DNS failure was an environment-access limitation, not evidence of an unhealthy site. |
| Pending changes | Service inspection reported no staged service changes; status also reported an old zero-change environment patch. No patch was committed or modified. |

No secret variable values, bearer tokens, account records, character payloads or
private rule bundles were inspected. No configuration, replica, volume, database,
networking or deployment changes were made. The current application remains
operational while Stage 0 prepares the future migration.

## Obsidian application observation

The user offered the open app for inspection. Computer-use inventory found one
Obsidian window, version 1.15.0, open in the PF2E vault with its release notes tab.
The first app-access request timed out. After the user restored the window,
read-only accessibility inspection succeeded. The visible GM pane shows current
location and exits, a room-creature/statblock roster and difficulty strip,
`Launch on site` and `Add to fight`, a capture box, live party stats, and a
`synced` combat status reporting no site encounter. Session start/end and clock/
rest controls are present. No action button was pressed, no content was typed,
and no setting, vault note or encounter was changed.

This verifies the installed UI's existing room-to-site controls and its displayed
connection state, not that every command succeeds or which repository commit is
installed. The plugin version/hash, command execution, history recovery and D&D
support remain unverified. Private campaign names, note text, character stats and
screenshots are deliberately not copied into this publishable package. PE-03
must compare installed behavior with source carefully rather than infer a match.

## Foundation and coverage baseline

See [foundation details](foundation-baseline.md). Existing SQL covers identity,
authorization and workflow metadata; character payloads/encounters remain file
and live-cache based. Both campaign and character identity validation exclude
D&D today. File writes plus SQL identity writes are not a shared transaction.

| Profile | Existing evidence anchors | Still required |
|---|---|---|
| PF2e | Snapshot/Pathbuilder ground-truth suites; committed fixtures such as `gavin_l11.json`, `amadeus_l11.json`, `goel_l10.json`, `kyle_l10.json`; `tests/pf2e_rules/` | Explicit per-feature authoritative coverage, source/rules pins, migration tests and non-live access evidence. PR6 review scaffolding activates no complete rules engine. |
| D&D 2014 | `tests/fixtures/dnd5e_ddb_rogue_2014.json`, D&D character/service/effect/combat tests | Installed-content attestation and real executed tests, shared ownership/drafts, cross-client action parity. |
| D&D 2024 | `tests/fixtures/dnd5e_ddb_paladin_2024.json`, D&D rehearsal/import/engine tests | Same boundaries, plus explicit edition-separation cases and complete agreed lifecycle. |

PE-02 owns the full coverage census. A test file's existence, a skipped test, or
a historical report is not fresh proof. Private data presence was not probed.

## Validation performed for this package

Documentation checks, local-link/dependency reconciliation, source-anchor checks
and independent agent reviews are recorded in PE-00. No product test suite was
run merely for a documentation package. Briefs name the checks their future
implementers must actually execute and report.
