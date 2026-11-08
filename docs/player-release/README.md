# Player release: Stage 0 handoff

The user approved the [implementation plan](implementation-plan.md) on 2026-11-08
and asked Codex to review existing Claude work and prepare briefs for both tools.
This package completes that review/briefing deliverable. Stage 0's contract,
coverage, reuse-review and visual-design tasks are still to be performed; this is
not a claim that every P0 exit gate is complete or that product implementation began.

## Start here

1. [Decisions](decisions.md): fixed product scope; do not repeat discovery.
2. [Baseline](baseline.md): remote main, local work, runtime evidence and limits.
3. [Task map](task-map.md): all stages and dependencies.
4. [Authoritative queue](../handoff/QUEUE.md): only claim a ready task.
5. [Handoff protocol](../handoff/PROTOCOL.md): branch, report, opposite-tool review.

The package lives on `docs/player-stage0` in the dedicated sibling worktree
`GM_pf2e_player_plan`, based on remote main
`7aeb9549c1df2807935a4a44229284e13a5add1d`.
The original `GM_pf2e` worktree is an older dirty remediation checkout; do not
use it as the feature baseline, reset it, or copy its pending files over main.

Before this package merges, documentation/review tasks can branch from its local
recorded commit. After integration, use current main and record the starting SHA.
If the package is still an uncommitted review copy, read it here but do not fork a
product implementation from it. Every implementation branch waits for its own
accepted dependencies; a broad approved plan does not remove those gates.

## First assignments

| Owner | Start | Deliverable | Review |
|---|---|---|---|
| Claude | [PE-01](../handoff/tasks/PE-01-state-command-contract.md) | Concrete state, command, permission and migration contracts | Codex |
| Codex | [PE-03](../handoff/tasks/PE-03-obsidian-reuse-review.md) | Assess four unmerged product commits plus merge ancestry; map safe ports and missing guarantees | Claude |
| Codex | [PE-02](../handoff/tasks/PE-02-rules-coverage.md) | Three-profile coverage/fixture matrix with verified versus unverified distinctions | Claude |
| Codex | [PE-04](../handoff/tasks/PE-04-ui18-review.md) | Review Claude's existing picker/rail patch without changing its source worktree | Claude fixes any findings; Codex rechecks |
| Claude | [PE-07](../handoff/tasks/PE-07-player-ux-design.md) | Tablet/laptop player journey mockups and compact phone companion | Codex technical review, user visual selection |

These five are independently ready for their bounded documentation/review/design
scope. Prefer PE-01 and PE-03 first in parallel. Do not run two tasks that mutate
the same template, migration head or shared application file simultaneously.
PE-05/06 carry forward UI-11 and its known HP inconsistency only after UI-18's
review/disposition. PE-10/11/12 are prepared but blocked on state/identity contracts.

Twelve forward task briefs plus this package's PE-00 report are provided. Later
task-map rows deliberately remain `needs brief`: accepted state/rules contracts
must supply their exact schemas and endpoints before a cold-start brief can be
truthful. This keeps every selected release feature scheduled without inventing
interfaces or disguising multi-thousand-line work as one small task.

## Existing work disposition

- **Reuse merged:** D&D rehearsal, PF2e rules evidence foundations, existing player
  sheets/builders, shared character workflows and DS-00 through DS-03.
- **Review before porting:** UI-18's uncommitted picker patch, UI-11's uncommitted
  presentation work, and the Obsidian v0.9 branch's recovery/provenance features.
- **Park without deleting:** website-wiki plans and the accepted but unmerged VTT
  platform. Its U-8 staging session remains open; it is not a player-release gate.
- **Preserve ongoing service:** all four systems' shipped tracker/builder/lookup/
  generators and maintenance. Cosmere/Mistborn player expansion follows this release.

Detailed audits: [player/design/rules](existing-player-work.md),
[Obsidian/VTT/wiki](existing-obsidian-vtt-work.md),
[state/access/economy](foundation-baseline.md).

## Paste-ready Claude kickoff

```text
Project: RPG Website
Task: PE-01 state/command contract (documentation only)
Read AGENTS.md, CLAUDE.md, docs/player-release/README.md,
docs/player-release/implementation-plan.md, docs/handoff/PROTOCOL.md,
and docs/handoff/tasks/PE-01-state-command-contract.md from the Stage 0 package.
The package is in the GM_pf2e_player_plan worktree on docs/player-stage0.
Claim PE-01 on claude/pe-01-state-command-contract using the package commit
until it is merged, or current main afterward. Record the base SHA.
Resolve the engineering contracts inside the approved product decisions.
Preserve independent build/play revisions and include character AND encounter
state in the transaction boundary. Review the existing Obsidian/VTT reuse
evidence; do not build competing protocols. No product code, live data, runtime
configuration, wiki or VTT implementation changes in this task.
Write the report in the PE-01 brief, update the queue, and return the exact
diff and checks to Codex for review. Do not claim source coverage as verified
unless PE-02 or an identified existing test/evidence record establishes it.
```

## Paste-ready Codex kickoff

```text
Project: RPG Website
Task: PE-03 Obsidian reuse review (source/test review and documentation)
Read AGENTS.md, CLAUDE.md, docs/player-release/README.md,
docs/player-release/existing-obsidian-vtt-work.md, docs/handoff/PROTOCOL.md,
and docs/handoff/tasks/PE-03-obsidian-reuse-review.md from GM_pf2e_player_plan.
Claim PE-03 on codex/pe-03-obsidian-reuse-review using the Stage 0 package
commit until merged, or current main afterward. Record both review SHAs.
Inspect feat/obsidian-session-operations-v0.9 through committed Git objects
or clean review checkouts. Preserve its original external worktree and vault.
Map each product commit to reuse/port/defer, check the listed tests on clean
baselines, and identify newer-main conflicts. Do not merge the old branch,
install the plugin, execute live commands, or edit product code.
Record exact results and limitations in the brief/review report; send the
handoff to Claude. After this, PE-02 and PE-04 are independent ready reviews.
```

No kickoff was sent automatically to Claude or another chat. The repository
documents are the shared memory; tool-specific chats do not share conversation
history. Original branches, app settings and live data remain unchanged.

## Stage 0 exit gates still owned by the briefs

- PE-01 accepted concrete contracts and a migration strategy.
- PE-02 an evidence-backed supported-content matrix and representative fixtures.
- PE-03 a bounded reuse decision so existing plugin recovery is not rebuilt.
- PE-04 the existing UI-18 verdict and an integration path for UI-11.
- PE-07 user-selected visual direction with provisional API bindings resolved
  against PE-01 before UI implementation.

Do not start blocked state/schema tasks until their named contract review is
accepted. Runtime configuration cutover, service provisioning, live migration
and deployment are later tasks with their own concrete evidence and authority.
