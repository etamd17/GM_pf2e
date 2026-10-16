# MAP-R3: Live `/map`: the TV gets the GM payload

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude (Codex reviews)
- **Branch:** `claude/map-r2-prep-scene-leak`, stacked on MAP-R2 (it reuses R2's `_scene_gm_only`).
  The two are reviewed and merged together.
- **Context:** `docs/vtt/design/vtt-alpha-plan.md` section 2, finding R3. `/map` stays the product
  until VTT M5.

## The bug
`/map/table` (the TV) is `@gm_required` and runs in a GM session. So it got exactly what the GM's
own window gets:
- over SSE: the GM `scene_update` for every saved scene, prep included, and the GM frame of every
  other event;
- over HTTP: `/api/scenes/<id>`, the GM payload.

That puts hidden tokens, GM-only lights and templates, closed secret doors and exact NPC HP in a
browser the whole room is looking at. The canvas skipped hidden tokens, but the data was there.

## Fix: a server-side table audience
- **SSE.**
  - The hub requests `audience=table` on `/map/table`, chosen by path because the socket can open
    before the body exists.
  - `/api/events` honours it only for a GM session; for a player it would add scene frames.
  - The subscriber entry gets a fifth field `'table'`. It keeps GM authority (field 1 True), so
    logout and revocation still close it.
- **Broadcast.** `sse_broadcast(..., table_filter=...)` computes a table frame once, like
  `player_filter`. When no filter is given, the table gets the player frame, never the GM one.
  - `scene_update`: the player projection, only for the scene on the table.
  - `scene_beacon`: only for the scene on the table.
  - `scene_activated`: always, so the TV can follow.
  - Replay serves the table frame from `_sse_table_frames`, a side map, so the buffer's
    `(id, gm, player)` shape and its tests are unchanged.
- **HTTP.** `GET /api/scenes/table` (`@gm_required`, plus the prefix gate) returns the player
  projection of the scene on the table, or 404. `map.js` uses it in table view.
- **Following a push.** The TV now reloads when another scene is activated. Before, only the
  empty "No active scene" screen did, so a TV showing scene A stayed on A.

## Acceptance
- [x] A table subscriber never receives a GM frame (scene or otherwise), live or replayed.
- [x] Prep-scene saves and prep beacons do not reach the TV.
- [x] `/api/scenes/table` has no hidden token and no exact HP; players get 403.
- [x] The table audience is refused to a non-GM stream, and GM logout closes table streams.
- [x] In a browser, `/map/table` reads only `/api/events?audience=table` and `/api/scenes/table`.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report (Claude, 2026-10-16)

**Files:**
- `app.py`: the SSE audience, `table_filter`, `_sse_table_frames`, the three scene broadcasts and
  `api_scene_table`.
- `templates/_sse_hub.html`: `streamUrl()`.
- `static/js/map.js`: the table read, and following a push.
- `core/route_policy.py`: `api_scene_table`.
- `CLAUDE.md`: the SSE bullet said "`?audience=table` is NOT a server-side feature"; it now
  describes the table audience.

**Tests:** `tests/test_map_r3_table_projection.py`, 11 tests:
- table-scene save: the TV gets the projection, without the hidden token or exact HP;
- prep save: nothing reaches the TV;
- activation reaches the TV;
- beacons reach it only on the table scene;
- other events give it the player frame;
- only a GM stream gets the audience;
- reconnect replay serves table frames;
- legacy GM logout closes the TV stream;
- `/api/scenes/table` content and 404;
- a real player gets 403;
- static checks of the hub and `map.js`.

**Verification:**
- With `app.py` reverted to MAP-R2 only, **8 of the 11 fail**. The 3 that still pass are guards
  that should hold either way: activation reaching the TV, players refused, and the static page
  check.
- With the fix: **11 passed**.
- `pytest tests/test_map_*.py tests/test_scenes.py tests/test_sse_*.py
  tests/test_campaign_switch_isolation.py tests/test_route_policy_inventory.py
  tests/test_auth_session_invite_transactions.py` -> **489 passed** (R2 and R3 tests included).
- `python tools/check_templates.py` -> 107 templates parse; `node --check static/js/map.js` is
  clean.
- **Browser** (local dev server, throwaway data dir), with a scene holding a visible token and a
  hidden token put on the table:
  - `/map/table` made exactly two requests, `GET /api/events?audience=table` and
    `GET /api/scenes/table`. Both returned only the visible token.
  - Adding a token from the GM side arrived live as `scene_update`, with the visible tokens and
    without the hidden one.
  - Pushing a second scene made the TV reload onto it.
  - No console errors.

**Limits:**
- If the scene on the TV is deleted, the TV's read returns 404 and shows an error toast that
  nobody at the TV can act on. This predates the change.
- The GM window's "Preview table view" still draws from GM data. It is the GM's own screen, so
  this is unchanged and intended.

<!-- ============ REVIEW (written by the reviewer) ============ -->

## Review

### Codex, 2026-10-16: ACCEPTED

No required fixes. The GM-authenticated TV gets the table frame on both live fan-out and replay,
and the player frame for other events. Prep scenes and prep beacons are withheld. The HTTP
read uses the existing player projection, and player sessions cannot acquire the table audience
or read its endpoint. The optional subscriber field preserves the existing revocation checks.
The page bootstrap contains the scene ID, not an embedded GM scene; initial reads use the table
endpoint and activation makes the TV follow the selected scene.

Independent verification: the R2/R3 tests, SSE security/revocation and replay tests, and route
policy inventory passed: **62 passed**. All **107 templates parse**, `node --check
static/js/map.js` and `git diff --check` pass. Claude's reported browser checks were reviewed;
Codex did not repeat the browser session. The documented deleted-scene behavior is pre-existing
and does not block these privacy fixes. Accepted for publication together with MAP-R2,
with merge gated on every CI job succeeding.
