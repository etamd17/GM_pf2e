# VTT rebuild coordination

The shared record between the two leads on the private PF2e VTT rebuild: Codex (platform
and integration) and Claude (client experience and adversarial design). The agents have no
private channel to each other. These files are that channel, and the user relays only a short
"new handoff: <id>" line between the two chats.

## Layout

| Path | Holds |
|---|---|
| `handoffs/` | One file per handoff, in the charter's `## VTT Handoff` format |
| `decisions/` | ADRs for anything that binds both sides (contracts, projections, scope) |
| `design/` | Longer proposals that a handoff points at |

## Where the files live

They live in the integration worktree,
`C:\Users\Evan\Documents\_gm_pf2e_ops_v2_remnant\GM_pf2e_vtt`, branch `side/vtt-rebuild`.
Both agents read and write them there by absolute path, even after each agent has its own
worktree. A copy on an agent branch only goes stale, because it would need a merge to show
the other side's handoffs.

Until the user authorizes commits, these files are untracked. `docs/vtt/` is the only
expected untracked path in this worktree, so a status baseline should list it and nothing else.

## Rules

1. **Handoff IDs.** IDs are `VTT-YYYYMMDD-NNN` and are unique across both agents. Before you
   write one, list `handoffs/` and take the next free number for the day. The file is named
   `<ID>-<author>.md`, for example `VTT-20261012-005-claude.md`.
2. **Append-only.** Never edit or delete a file the other agent wrote. To accept, challenge or
   supersede one, write a new handoff that cites its ID. You may append a dated
   `Update (<author>, <time>)` line to your *own* handoff. Rewriting one already sent is not
   allowed.
3. **Status lives in the latest handoff.** There is no shared index file to edit, so two
   agents can never collide on it. The newest handoff that cites an ID is its current state.
4. **Claims need evidence.** "Tests pass" means the exact command and fresh output, as the
   charter requires.
5. **No approvals by file.** A handoff never authorizes a merge, push, deploy, commit or
   cross-branch edit. Those come only from the user in chat.
6. **ADRs.** ADRs are `decisions/VTT-ADR-NNNN-<slug>.md`, numbered separately from the
   app-wide `docs/adr/`. An ADR that binds both sides lists both leads as approvers and
   stays `proposed` until both have accepted it in a handoff. A decision the user made
   directly is recorded as `accepted (user)`.
7. **No emojis**, per CLAUDE.md.

## Relaying (for the user)

When an agent finishes a handoff, tell the other chat:

```text
New VTT handoff: VTT-20261012-NNN. Read docs/vtt/handoffs/ in GM_pf2e_vtt.
```

## History note

Handoff ID `VTT-20261012-001` was issued twice, once by each agent, before this directory
existed. Both are kept, and the author suffix tells them apart. Numbering is unique from
`002` onward.
