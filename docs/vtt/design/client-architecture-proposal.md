# VTT client architecture proposal (Gate 1, client side) -- v1 (2026-10-12)

Author: Claude (client experience and adversarial design lead)
Status: proposal, not accepted. Nothing here is a contract until both leads approve an ADR.

## 1. React versus raw PixiJS ownership

Rule: React owns chrome; Pixi owns the map; neither reaches into the other's tree.

- React renders: top status bar, tool rail, right dock (inspector, token sheet summary, layer
  list), bottom tray (initiative, macros, chat/rolls), dialogs, toasts, connection banner.
- A single `<MapHost>` component mounts one `<div>` and hands it to a `SceneRuntime` class
  (plain TypeScript) on mount, disposes it on unmount. React never renders Pixi children,
  no `@pixi/react`.
- `SceneRuntime` subscribes to Zustand with `store.subscribe(selector, listener)` outside
  React, diffs entity maps by id+version, and creates/updates/destroys display objects.
- Pointer input on the canvas is handled inside the runtime (tool state machine). The runtime
  emits intents (`moveIntent`, `selectIntent`, `measure`, `ping`) into a command layer; it
  never calls the transport directly.
- React reads only coarse derived state from the store (selected ids, active tool, connection
  status). Hot data (token x/y during drag, cursor positions) never goes through React state.

## 2. Scene graph layers and render invalidation

Stage children in fixed z-order, each a Container with its own dirty flag:

1. `background` -- map image or video sprite; tiled for > GPU max texture (4096 needs 1 tile on
   most GPUs, but tiles of 2048 are safe for 1080p laptops and iGPUs).
2. `grid` -- one Graphics, rebuilt only on grid calibration change or zoom bucket change.
3. `terrain/tiles` -- static decoration; cacheAsTexture where stable.
4. `templates` -- area templates and auras.
5. `walls/doors` -- GM/assistant only by projection; authoring handles in an overlay sub-layer.
6. `tokens` -- one Container per token (sprite, ring, nameplate, bars, condition pips).
7. `lighting` -- light glow, additive blend.
8. `vision/fog` -- mask texture from the AUTHORITATIVE visibility polygon(s) in the projection
   (see contract decision D6). The client never decides what is hidden from a player.
9. `measure` -- rulers, drag ghosts, movement path, cost labels.
10. `effects` -- pings, targeting lines, turn highlight. Only layer allowed to animate on the GM
    view (carry-over of the legacy "animation confined to table screen" rule, ping excepted).
11. `ui-overlay` -- selection boxes, marquee, snapping guides.

Invalidation: event-driven render, not a free-running ticker. `app.ticker` is stopped; a
`requestRender()` schedules one frame via rAF; animations hold a render lease while active.
Two-level cache keys (cheap version check, then content signature) for expensive layers, the
same lesson as the legacy fog mask. Nothing animated enters a vision/fog cache key.

WebGL recovery: listen for `webglcontextlost` (preventDefault) and `webglcontextrestored`;
on loss, freeze input, show a "Renderer lost -- restoring" banner, destroy the Pixi app, rebuild
from the store snapshot on restore or after a 3 s timeout with a fresh canvas. Store state is
untouched, so recovery is a pure re-render. A forced-loss dev hook
(`WEBGL_lose_context`) backs a Playwright test.

## 3. Zustand live state versus TanStack Query

Zustand (hot, socket-fed, versioned):

- `session` -- `principal.kind` (`member` | `table`), `campaignRole` (members only: gm,
  assistant, player, observer), effective capability list, campaign id. Table is a device
  principal, not a role (VTT-20261012-006/007).
- `connection` -- status machine (see section 6), current `subscriptionId`, last applied
  dense sequence for that subscription, latency sample.
- `scene` -- current scene projection: entities keyed by `(kind, id)`, each carrying an opaque
  version token (equality only, never arithmetic); the scene's own opaque token; nothing the
  projection did not contain.
- `pending` -- outbound durable commands by `commandId` with optimistic overlays; overlays are
  layered over `scene` at read time, never written into it.
- `transient` -- drag previews, cursors, pings, rulers from other clients; TTL-expired.
- `ui` -- active tool, selection, viewport (pan/zoom), dock panel, reduced-motion pref.
- `combat` -- initiative order and turn pointer as projected (read model only).

TanStack Query (cold, request/response, cacheable):

- scene list, asset catalog and thumbnails, compendium/bestiary lookups, actor sheets detail,
  macro presets, campaign membership roster, chat history pages.
- Query keys are always prefixed with `campaignId`; switching campaign clears the client.
- A `vtt.patch` that touches catalog-backed data invalidates the relevant query key; the socket
  never writes into the Query cache directly.

Rule: one source of truth per datum. If it changes during a turn, it is Zustand; if it is
browsed, it is Query.

## 4. Transport abstraction

```ts
interface VttTransport {
  connect(): void; disconnect(): void;
  subscribe(scope): void;                          // vtt.subscribe
  sendCommand(cmd): void;                          // vtt.command, ack correlated by commandId
  sendTransient(evt): void;                        // vtt.transient, fire-and-forget, throttled
  sendWhisper(msg): void;                          // vtt.whisper.send
  on(handler: (msg: ServerMessage) => void): Unsubscribe;
}
```

- Implementations: `SocketIoTransport` (production), `MockTransport` (deterministic, scripted
  server for unit/component tests and for Gate 2 before the backend exists), and
  `LoopbackTransport` (multiple in-page clients sharing one fake server, used for UX work on
  role projections).
- A single `MessageRouter` validates every inbound message against the generated runtime schema
  before it touches the store. A message that fails validation triggers resync, never a partial
  apply.
- Patch application (agreed in VTT-20261012-006/007): every durable frame carries
  `subscriptionId` and a dense `seq`. Frame for another `subscriptionId` -> discard. `seq <= last`
  -> duplicate, ignore. `seq > last + 1` -> gap, enter resyncing. A snapshot replaces the whole
  subscription state atomically and sets the baseline. A scene switch or Redis loss rotates
  `subscriptionId` and is followed by a fresh snapshot. Patches are whole-entity replacements
  plus explicit removals; omitted fields are never merged. For the table, hidden and deleted
  are indistinguishable by design.
- Command lifecycle: `queued -> sent -> acked(applied) | rejected(code) | timed-out`. Timed-out,
  `rate-limited` and `temporarily-unavailable` commands are retried with the SAME `commandId`
  (server dedup makes this safe). A corrected command after `stale`, `conflict`,
  `validation-failed` or `rule-violation` gets a NEW `commandId` with fresh expected tokens.
- Transient channel: drag previews throttled to rAF and 20 Hz on the wire, latest-wins,
  never acknowledged, never persisted.

## 5. Role projections and workspaces

The client renders what the projection contains and nothing else. Role decides chrome only.

| Role | Chrome | Map input | Notes |
|---|---|---|---|
| GM | full rail, all layers, authoring tools | all | "Preview as" a player via a server-produced projection, not a client filter |
| Assistant | rail minus campaign admin; capability-gated tools | per capability | exact capability set is a joint decision (D3) |
| Player | reduced rail: select, measure, ping, target; own sheet dock | owned tokens only | sees only own and shared vision |
| Observer | no rail; follow-cam toggle; chat read | none | read-only, sanitized |
| Table | no chrome at all; full-screen map; small connection dot | none | pairing code flow; animation allowed; self-reload on deploy |

Table is a separate route bundle entry (`/vtt/table`) that does not ship authoring code.

## 6. Connection, stale edits, rejection, resync, accessibility

Connection state machine: `connecting -> live -> degraded (no heartbeat > 5 s) -> reconnecting
(backoff 0.5 s .. 10 s, jitter) -> resyncing -> live`, plus terminal `revoked` (role removed or
pairing revoked) and `version-mismatch` (client build older than server schema -> hard reload
prompt; table auto-reloads).

- While not `live`, durable map input is disabled, transient input is dropped, and a persistent
  banner states the condition. Pending commands stay visible with a "waiting" marker.
- Rejection UX: the optimistic overlay animates back (or snaps, under reduced motion), and a
  toast names the reason in plain language. One behaviour per code, as proposed in
  VTT-20261012-007 item 3b:
  - `stale`, `conflict`: revert, then offer a retry with a new commandId.
  - `validation-failed`, `rule-violation`: revert, then show the allowlisted reason.
  - `unauthorized`: revert with a generic message that never says whether the target exists.
  - `authentication-required`: run the re-auth flow.
  - `rate-limited`, `temporarily-unavailable`: keep the command pending and retry it with
    backoff.
  - `resync-required`: enter resyncing.
  - `unsupported-version`: enter the version-mismatch state.
  - `recipient-offline`: show a whisper-only failure notice.
  - `command-id-reused`, `invalid-schema`, `unknown-command`, `server-error`: treat as a client
    bug. Revert, log, and show a generic error, never the raw detail.
- Stale edit (versions moved under an open inspector field): carry over the legacy rule -- the
  field being edited is never repainted; it shows "changed by <who>" with Keep mine / Take theirs.
  Keep mine re-issues under a new commandId with the fresh expected token.
- Reduced motion: honour `prefers-reduced-motion` plus a per-user override; pings become static
  rings, token moves snap, no flicker.
- Keyboard: every map action reachable without a pointer -- Tab/arrow selection cycle across
  visible owned tokens, arrows move one square (each step a transient, Enter commits), `M`
  measure, `P` ping at selection, `Esc` cancels; focus is announced through an ARIA live region
  ("Valeros selected, 3 squares from Goblin Warrior").
- Accessibility: chrome built on React Aria primitives; the canvas has an off-screen list
  mirror of visible tokens for screen readers; colour is never the only signal for
  ownership/target/hostility; two typefaces only (Cinzel display, Inter UI) via tokens, also
  for canvas text.

## 7. Multi-client Playwright scenarios

All run with separate browser contexts per role against the real stack (and a subset against
MockTransport in CI-lite). Each asserts on both rendered DOM/canvas state AND the raw socket
frames received, because a leak in the payload is a failure even if nothing renders.

1. Hidden token: GM hides an NPC; player, observer and table frames never contain its id,
   name, position or HP, before or after reconnect.
2. Secret door: player frames never contain the wall; after GM reveals, it appears as a door.
3. Private vision: two players with separated tokens; each receives only own vision polygon
   and entities inside it.
4. Reconnect: kill the player's socket mid-drag; drop is not committed; on reconnect the
   snapshot reconverges within 5 s; no duplicate move when the same commandId is resent.
5. Server restart: commit a move, restart web + Redis, all four roles reconverge to it.
6. Conflicting drags: GM and owning player drag the same token; exactly one commit wins, the
   loser sees a stale rejection and the overlay reverts.
7. Forged ownership: player client sends move for an unowned token via devtools-level
   injection; rejected, no state change, no information in the rejection about the target.
8. Role revocation: GM removes a player's membership mid-session; that client goes `revoked`
   within one heartbeat and receives no further frames.
9. Scene publication: unpublished scene invisible to players/table; publishing switches table
   and players; unpublishing removes it without leaking the draft.
10. Table pairing: expired/revoked code rejected; paired table has no command capability.
11. Whisper to offline recipient: sender sees a clear failure; nothing persisted.
12. Cross-campaign: a client subscribed to campaign A cannot subscribe to a scene of campaign B
    by id guessing.
13. WebGL loss: forced context loss recovers without a reload and without state change.

## 8. Smallest backend contract decisions needed before a walking skeleton

Status 2026-10-12: D1-D9 are agreed in direction between the leads (VTT-20261012-005, -006,
-007), with Codex's refinements accepted. The settled forms are in those handoffs and go into
the proposed VTT-ADR-0002, which Codex drafts. The option lists below are kept as the original
record. Where they differ from the handoffs, the handoffs win.

(Listed as decisions with options, not fields.)

- D1 Envelope and versioning: one `schemaVersion` integer for all messages versus per-message
  versions; how a client learns it is too old.
- D2 Snapshot/patch model: entity-level replace patches (simple, larger) versus JSON-Patch style
  ops (smaller, harder to sanitize per audience). Recommendation: entity-level replace keyed
  by kind+id+version, with explicit `removed` lists, because per-audience sanitization must
  happen per entity anyway.
- D3 Role and capability vocabulary: fixed role set with capability lists in the snapshot
  versus capability-only. Client needs the list to gate chrome; server enforces regardless.
- D4 Sequence numbers: per-scene monotonic version only, versus also a per-connection delivery
  sequence for gap detection on non-scene events (combat, chat).
- D5 Ack shape and error codes: closed enum of rejection reasons the client can render.
- D6 Visibility representation: server sends visibility polygons per audience versus sends only
  visible entities and lets client draw fog from them. Privacy requires the first for walls
  the player cannot see; cost is server-side raycasting (legacy measured ~138k segment checks).
- D7 Transient relay rules: who may see whose drag previews and cursors (player A must not see
  a hidden token being dragged by the GM).
- D8 Table pairing flow: code length, TTL, what identity the table holds.
- D9 Scene subscription scope: one scene per connection versus campaign-wide with scene focus.

## 9. Carried over from the legacy map (evidence, read-only, commit b8c7fed6)

Confirmed: Flask 3.1.3 + Jinja + vanilla JS, no build step (only package.json is the dev-only
tools/tailwind). app.py is 25,778 lines (CLAUDE.md's "~22k" is stale). Legacy map = Canvas 2D
`static/js/map.js` (3,578 lines), `core/scenes.py` (SCHEMA_VERSION 3, JSON per scene on the
volume), `services/scene_sync.py::project_scene` (the only GM/player boundary), ~17 routes in
app.py:9235-10311, ~389 `def test_` across 21 map test files, mostly source-string assertions
rather than browser tests. Sync is SSE only: in-process `queue.Queue` fan-out
(app.py:2606-2629, 2791-2888), 256-frame replay ring, `is_gm` captured once at connect,
single gevent worker mandatory. Combat is process-global (`ACTIVE_ENCOUNTER`, `TURN_INDEX`,
`ROUND_NUMBER`, `ENCOUNTER_LOCK` RLock, app.py:2199-2280), one live campaign slot, flushed to
JSON by a 2 s thread. Postgres exists only for identity and characters (ADR-0002).

Rules the new client inherits:
- The GM's in-progress edit is sacred: never repaint a focused or dirty field (AUDIT Round 4).
- Undo is a compensating action through the same command path, never a restore.
- Animation confined to the table view (ping excepted); nothing animated in a mask cache key.
- Two-level cache keys; `version` bumps on every save so it cannot be a content key alone.
- Two typefaces (Cinzel, Inter) via tokens, canvas text included.
- `[hidden]` vs author `display` bug class: forbid `hidden` attributes on styled elements in
  the new client, or ship a lint/test.
- One visibility rule, server-side. "NPC unnamed until revealed" is still an open design
  question in AUDIT Round 3 and must be settled on the combatant side, not in the map.
- Rendering was never the bottleneck (0.7 ms/frame legacy scene); write and auth paths were.

## 9a. User decision 2026-10-12: table-first, players later

Build order for the client: GM workspace and display-only table first. Player, observer and
assistant workspaces are designed (contracts stay audience-aware, so D3 and D6 are still
decided now) but not built until the user says so.

Consequences:
- Gate 2 is unchanged: GM plus table, which was already the joint acceptance pair.
- Gate 3 is narrowed to GM plus table. Steps that need a player client (owned-token drag,
  forged ownership from a player) become server-side contract tests driven by a scripted
  client, not browser workspaces.
- Playwright scenarios 1, 2, 9, 10 and 13 run against the table audience now. Scenarios
  3, 6, 7, 8 and 11 are deferred with the player workspace. Scenarios 4, 5 and 12 run GM plus
  table.
- D6 (server-side vision) is no longer on the critical path. The table still needs a
  server-produced table projection, which is where the legacy leaks (R2, R3) must not recur.
- AUDIT.md's "not per-player devices" stays true for now. CLAUDE.md needs one amendment:
  the new VTT exists and is table-first; per-player is deferred, not rejected.

## 10. Risks found during review

R1 (decision for the user). CLAUDE.md and docs/map/AUDIT.md:76-82 record as settled, "do not
re-litigate": map is GM-only, audience is one shared table screen and NOT per-player devices.
The charter requires remote players with private per-player vision. The charter is the newer
instruction, but until CLAUDE.md is amended every agent reading it is told the opposite.

R2 (live, outside VTT scope). `_broadcast_scene` (app.py:9137) sends a player-projected
`scene_update` to every player SSE subscriber on every save, including unactivated prep scenes
(app.py:9316, under a comment saying it is NOT pushed). Leaks scene names, full wall geometry
and non-hidden tokens of prep scenes to anyone watching the event stream. Fog is not applied
server-side. Untested. Flagged as a separate task.

R3. The legacy table view is a GM session receiving the GM payload; exact NPC HP bars and
GM-only lights render on the TV (map.js:391, 1414). The new table projection must be its own
audience, which the charter already requires; the legacy behaviour is the regression to test.

R4. Pixi is not justified by legacy performance (Canvas 2D had 24x headroom). It is justified
only by the much larger reference scene (100 tokens, 500 walls, 20 lights, video). Recommend
a short Gate 2 spike rendering the reference scene in both before committing permanently.

R5. Server-side per-audience vision (D6) is the most expensive backend item and the one most
likely to slip; the legacy client raycasts ~138k segment checks per mask. The walking skeleton
should not depend on it, but no player-facing scene may ship before it.

R6. Production is one gevent worker serving SSE; adding Socket.IO + Redis + Postgres + MinIO +
a worker + a Node build step changes the Railway deploy shape. Needs an integration decision
before any of it lands on side/vtt-rebuild.

R7. No browser test harness exists for the map (tests/browser/ has one character test). The
Playwright multi-client harness is new infrastructure, not an extension.
