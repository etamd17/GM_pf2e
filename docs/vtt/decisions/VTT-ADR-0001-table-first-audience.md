# VTT-ADR-0001: Table-first audience, per-player clients deferred

- Status: accepted (user), 2026-10-12
- Approvers: user (decision). Codex and Claude acknowledge in handoffs.

## Context

The dual-agent charter targets remote players with private per-player vision.
`CLAUDE.md` and `docs/map/AUDIT.md:76-82` record the opposite as settled for the legacy map:
the map is GM-only and its audience is one shared table screen, not per-player devices.

## Options

1. The charter governs: build per-player remote vision now.
2. Table first, players later: build the GM workspace and the display-only table first, and
   design for players without building them.
3. Keep the legacy decision: per-player devices stay permanently out of scope.

## Decision

Option 2, chosen by the user on 2026-10-12.

## Consequences

- The GM workspace and the display-only table are built first. Player, observer and
  assistant workspaces are designed but not built until the user reopens them.
- Contracts stay audience-aware. The role and capability vocabulary (D3) is still decided
  now, so adding players later is not a schema break.
- Server-side per-audience vision (D6) leaves the critical path. The table still gets its own
  server-produced, sanitized projection. It never receives a GM payload that the client hides.
- Gate 3 narrows to GM plus table. Owned-token and forged-ownership checks become backend
  contract tests driven by a scripted client.
- Playwright scenarios that need a player workspace are deferred. The proposal lists which
  ones in `design/client-architecture-proposal.md`, section 9a.
- `CLAUDE.md` needs one amendment, made once commits are authorized: the new VTT exists and is
  table-first, and per-player play is deferred, not rejected. The legacy `/map` stays GM-only
  until the new VTT replaces it.
