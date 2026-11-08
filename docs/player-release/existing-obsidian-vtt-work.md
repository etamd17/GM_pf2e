# Existing Obsidian, VTT, and campaign-workspace work

Read-only source audit: November 8, 2026. Baseline: local `origin/main` at
`7aeb9549c1df2807935a4a44229284e13a5add1d`; the coordinating Stage 0 task separately
verified that remote main matches. No product source, original worktree, running
service, or vault content was changed. Tests and deployments were not run.

## Branch and working-tree evidence

Counts are `git rev-list --left-right --count origin/main...HEAD`: commits unique
to main, then commits unique to the inspected branch. They describe ancestry,
not the amount of unmerged product work. `clean` means `git status --short`
returned no entries during this audit.

All `GM_pf2e_*` paths below are under
`C:/Users/Evan/Documents/_gm_pf2e_ops_v2_remnant/`.

| Worktree | Branch / HEAD | Main-only / branch-only | Working tree and disposition |
|---|---|---|---|
| `GM_pf2e_vtt` | `side/vtt-rebuild` / `0e244a27` | 232 / 108 | Clean. Authoritative VTT coordination/handoff archive; preserve and park new VTT work. |
| `GM_pf2e_vtt_claude` | `agent/claude-vtt-client` / `73d857fd` | 232 / 13 | Clean. Older client development snapshot, not the latest integrated implementation. |
| `GM_pf2e_vtt_claude_platform` | `agent/claude-vtt-platform` / `00b334b4` | 80 / 107 | Clean. Latest integrated VTT implementation inspected. Local remote-tracking counterpart has the same head. |
| `GM_pf2e_vtt_codex` | `agent/codex-vtt-platform` / `c5653e05` | 232 / 2 | Clean. Early canonical-protocol snapshot; do not mistake for current platform state. |
| `GM_pf2e_vtt_bundle` | `codex/vtt-bundle-split` / `00b334b4` | 80 / 107 | Clean; identical head to integrated platform. Its accepted work is already on that platform branch. |
| `GM_pf2e_vtt_u1` | `codex/vtt-multi-select` / `4ed42031` | 80 / 106 | No tracked diff; untracked `frontend/node_modules/`. Multi-select is already in the platform head. |
| `GM_pf2e_pr6b3a` | `codex/5e-06-image-gc` / `1d98710e` | 187 / 0 | No tracked diff; two untracked campaign Markdown planning files, listed below. Its committed HEAD is an ancestor of main. |
| External `gm_pf2e_console` | `feat/obsidian-session-operations-v0.9` / `1d0b895e` | 253 / 5 | Committed source readable; working-tree status could not be established because Git reported a worktree/permission error. Preserve pending contents; do not describe it as clean. |

External Obsidian worktree:
`C:/Users/Evan/.codex/visualizations/2026/09/10/01a08c38-5e30-71c1-a88d-ca9fba2728c3/gm_pf2e_console`.
Its `.git` pointer resolves to the shared repository's
`.git/worktrees/gm_pf2e_console`. Normal and explicit-worktree status attempts
failed; this audit did not change Git configuration or escalate permissions.
Committed Obsidian evidence below was read with `git show` from the shared repo.

Neither `00b334b4` nor `1d0b895e` is an ancestor of `origin/main`.
Existing main-branch Obsidian functionality remains a separate shipped baseline;
an old branch being unmerged does not imply the integration is absent from main.

## Obsidian: implemented recovery work to review and reuse

The v0.9 branch contains five branch-only commits, one of which is a merge:

- `4f100aa9`: hardened encounter console.
- `5dd705e8`: canonical session packages.
- `a991e379`: merge of the then-current main.
- `fd223136`: canonical session provenance guards.
- `1d0b895e`: durable session-evidence recovery.

The branch-only diff from its merge base covers 13 files, including `app.py`,
`core/obsidian_sync.py`, `services/obsidian_sync.py`, the plugin, route policy,
and four Obsidian test files. These are implemented code and tests, not merely
an intention in a queue. No independent acceptance record or fresh passing test
run was established for this branch in this audit.

Evidence at `feat/obsidian-session-operations-v0.9`:

| Existing work | Source evidence | Release relevance |
|---|---|---|
| Room-to-campaign checks, typed area HP, initiative editing, capability status | `tools/obsidian-plugin/main.js`; `tests/test_obsidian_plugin_behavior.py:1025` onward | Reuse guards and UI behavior while adding D&D adapters. |
| Canonical package identity and source-note preservation | `tools/obsidian-plugin/README.md`, section "Version 0.9 session contract"; behavior tests at line 1049 onward | Package binding reads the canonical run sheet, record and closeout; generated evidence is separate. Preserve authored-note protection. |
| Remote acknowledgment separated from failed local writes | `main.js:1741`, `main.js:1785`; behavior test at line 1087 | `pendingMaterializations` retains acknowledged state for explicit local retry even after the current session changes. |
| Complete paginated drain of the existing integration event stream and duplicate protection | `main.js:2784`; behavior test at line 1097 | Port existing event-ID/sequence deduplication and serialized draining rather than rebuilding them. This does not make the server's event coverage complete. |
| Connection-scoped queues, reset behavior and in-flight generation guards | `main.js:1278`, `main.js:1435`; behavior tests at lines 1104-1120 | Protect campaign/website switches and quarantine legacy commands lacking trusted scope. |
| Read-only room-manifest preflight | `services/obsidian_sync.py:718`; `main.js:2167` | Reuse reference validation and capability negotiation; extend to explicit starting effects and revision-bound atomic launch. |
| Machine-owned output containment and collision protection | Plugin README; behavior tests at lines 1073-1079 | Preserve schema/session validation, ownership markers and refusal to overwrite narrative notes. |

Limits that remain after reuse:

- The branch remains PF2e-oriented. It does not supply the agreed D&D
  2014/2024 launch/control parity.
- Its JSONL ledger still appends an event before separately saving runtime and
  command receipts (`services/obsidian_sync.py:870`, `:882`). It is not an atomic
  gameplay-state/event transaction.
- It mirrors the integration's existing events; website/player-originated
  actions, automatic consequences, economy and durable pending action stages
  still need the new common command/history path.
- Preflight validates stages/creatures/reminders/reveals; it is not yet the
  approved structured starting-effect preview plus atomic roster/effect commit.
- Canonical package requirements, closeout metadata and typed override reasons
  are existing branch behavior, not new decisions made by this audit. Preserve
  compatibility where needed without making unrelated narrative-package rules a
  prerequisite for character play or D&D integration.

Recommended treatment: review and port bounded recovery/provenance slices onto
the current baseline, carrying their tests. Reconcile current authorization and
player-workflow changes explicitly. Do not wholesale merge the old branch or
create competing replacement recovery code before this comparison.

## VTT: accepted implementation, parked launch

The canonical coordination directory is
`GM_pf2e_vtt/docs/vtt/`, per its `README.md`; copies of its handoffs on other
worktrees are intentionally incomplete/stale. Latest inspected handoffs:

- `handoffs/VTT-20261023-138-claude.md`: U-8 kit merged; multi-select accepted.
- `handoffs/VTT-20261023-140-claude.md`: multi-select merged; bundle split
  accepted with a small reload-message suggestion.
- `handoffs/VTT-20261023-141-codex.md`: final bundle split published at
  `00b334b4`; reload suggestion included. The handoff says ready to merge,
  while current platform HEAD proves it is now integrated there.
- `handoffs/VTT-20261021-122-claude.md`: action-consumer implementation accepted
  and merged, with the explicit limitation that unresolved area attempts are
  held only in page memory and lost on reload.

Handoff 140 reports 907 client tests across 59 files, typecheck and bundle checks;
141 reports the focused reload follow-up. These are historical reported results,
not tests rerun by Stage 0. The user-driven U-8 timed staging session remains open
in the latest record. No production launch or completed U-8 acceptance is claimed.

`docs/vtt/design/production-plan.md` has a stale "revised draft" header; section 7
records later user decisions and section 6 the revised R-1 sequence. Its former
map-replacement/code-image/identity launch sequence is historical context, now
parked by the approved player-first direction. Do not restart it from a stale
queue entry or treat it as a prerequisite for player release work.

Useful implementation references at `agent/claude-vtt-platform` / `00b334b4`:

- `vtt/commands/service.py`: request digests, command-ID reuse rejection,
  authorization checks, serialized campaign stream, expected versions,
  deterministic receipts, and state/receipt/outbox commit ordering.
- `vtt/storage/writes.py`, `vtt/storage/stream.py`, `vtt/storage/models.py`:
  transactional scene writes and stream/outbox records.
- `vtt/delivery/hub.py`, `vtt/projection/serializers.py`, protocol contracts and
  corresponding delivery/PostgreSQL tests: projection and reconnect patterns.
- `frontend/vtt/src/protocol/session.ts`, its tests, and
  `frontend/vtt/src/app/controls/areaAttemptStore.ts`: useful uncertain-outcome
  UX and subscription handling examples.

These are patterns to assess for extraction, not a mandate to import VTT tables,
Socket.IO, the React renderer or map controls into the player release.
`vtt/live.py` explicitly mirrors tracker process globals into SQL actor records;
it does not make combat state transactional. The existing VTT outbox is a
delivery stream, not proof of a complete permanent gameplay history. Its
page-memory attempt store does not satisfy the newly agreed reload recovery.

Keep shipped legacy `/map` maintenance separate from the unmerged VTT rebuild.
New spatial work and VTT launch remain parked; retain all branches and evidence.

## Website wiki / Markdown workspace: plans only

Two untracked files in `GM_pf2e_pr6b3a`:

- `docs/superpowers/specs/2026-10-12-campaign-markdown-workspace-design.md`
- `docs/superpowers/plans/2026-10-12-campaign-markdown-workspace.md`

The specification states approval for implementation planning only. The plan
states that no product code was written and a later explicitly authorized build
was intended. This audit found these two untracked planning files, not an
implemented campaign-content subsystem in that worktree. Its commit history is
the already-merged 5E-06 image-cleanup lineage.

The old proposal makes the website authoritative for campaign documents and
Obsidian optional. That product direction conflicts with the new Obsidian-owned
GM-prep direction and must not be resumed automatically. Park the proposed
editor, document repository, publishing/search system and document-sync bridge.
Retain the plans as reference for owner-private content, reviewed publication,
exact save receipts, account-scoped browser recovery and authorized replay.
Those ideas do not require shipping the wiki or adopting its document schema.

## Bounded follow-on brief inputs

| Candidate task | Dependency and scope | Exit evidence |
|---|---|---|
| Obsidian v0.9 recovery reconciliation | Before new P6 recovery code: inspect the four product commits against current main; map safe ports, conflicts and existing behavior that must remain. Resolve external worktree status before any mutation there. | Per-file reuse/port matrix; relevant contract/behavior tests rerun on the chosen clean integration baseline; no secret/vault files copied. |
| Shared command receipts and history foundation | P1 state repository contracts first. Assess VTT transaction/receipt/outbox patterns without importing its UI or mirror authority. | Same-ID retry and changed-body rejection; atomic state/event commits; crash/restart and PostgreSQL contention evidence; authorized snapshot/replay watermark. |
| Supported action lifecycle | Shared state/history contracts; PF2e and D&D edition adapters. Define costs, pending dice/saves/reactions, cancellation, correction and resumable outcomes. | Player/GM/plugin parity, physical/digital fixtures, no duplicate costs, durable pending work and no secret leakage. |
| System-aware room preview and launch | Action/effect and history contracts; reuse Obsidian preflight/capabilities. Add versioned structured manifests, explicit effects and revision-bound commit. | PF2e/D&D 2014/D&D 2024 fixtures; invalid/stale preview preserves current encounter; roster/effects commit once; explicit replacement confirmation. |
| All-origin Obsidian mirror | Common ledger plus reconciled v0.9 client recovery. Include website/plugin actions, automatic effects, approvals, loot, crafting and outcomes. | Paged catch-up and restart produce one logical history; campaign switches cannot mix evidence; generated files preserve authored notes. |

These are decomposition inputs for executable briefs, not claims that the whole
listed capability fits one small PR. High-risk implementation remains assigned
through the current handoff protocol; this audit did not claim or start those
tasks, modify the shared queue, or message other chats.
