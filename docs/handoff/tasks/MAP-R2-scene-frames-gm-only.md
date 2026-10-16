# MAP-R2: Live `/map`: scene frames reach player connections

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude (Codex reviews)
- **Branch:** `claude/map-r2-prep-scene-leak` from `origin/main` (`20b47773`)
- **Context:** `docs/vtt/design/vtt-alpha-plan.md` section 2, finding R2. The live `/map` stays the
  product until VTT M5, so its leak is fixed here.

## The bug
`sse_broadcast(event, data, player_filter=...)` sends `data` to GMs and `player_filter(copy)` to
players; an omitted filter sends `data` to everyone. Three map events reached player connections:
- `scene_update` (`_broadcast_scene`) sent the player projection of **every saved scene**, prep
  included, to every player subscriber in the campaign: scene name, map, visible tokens and lights.
- `scene_activated` had no filter, so players got the GM's raw payload (which scene is on the TV).
- `scene_beacon` passed the GM's pings and ruler through unchanged.

No player page listens to any of these; only `static/js/map.js` does, and it is loaded only by
`/map` and `/map/table`, both `@gm_required`. So the frames are dropped for players outright
rather than filtered to the active scene. (The TV runs as a GM session, so it still receives
them. Giving it its own table projection is MAP-R3.)

`scene_mood` is not a map event: it drives the player-facing ambience on the sheet and player view,
and is unchanged.

## Acceptance
- [x] A player SSE subscriber receives no `scene_update`, `scene_activated` or `scene_beacon`, live
      or from the replay buffer; a GM subscriber still receives all three.
- [x] A structural test fails if any future `sse_broadcast('scene_*')` (except `scene_mood`) is
      not GM-only.
- [x] Map, scene and SSE suites stay green.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report (Claude, 2026-10-16)

**Change (`app.py`):** a `_scene_gm_only` filter that returns `None`, passed as `player_filter` by
`_broadcast_scene` (`scene_update`), both `scene_activated` broadcasts in the activate route, and
`scene_beacon`. `_broadcast_scene` no longer builds a player projection at all, which also saves a
projection per scene save. The table projection itself (`_scene_payload(..., player=True)`) is
untouched; MAP-R3 will use it.

**Tests:** `tests/test_map_r2_scene_frames.py`, 7 tests, all driving the real routes against a GM
queue and a player queue:
- saving a prep scene with a token: GM gets `scene_update` with the token; player gets nothing and
  no scene or token name;
- activating a scene: GM gets `scene_activated` and `scene_update`; player gets neither, nor the id;
- beacon (ping, measure, clear): GM gets `scene_beacon`; player queue empty;
- the replay buffer stores `None` as the player copy of every scene frame (a reconnecting player
  with `Last-Event-ID` is served from there);
- AST guard over every `sse_broadcast('scene_*')` call in `app.py`.

**Verification:**
- On `main` (fix reverted): **7 failed**. With the fix: **7 passed**.
- `pytest tests/test_map_*.py tests/test_scenes.py tests/test_sse_event_replay.py
  tests/test_sse_security_and_revocation.py tests/test_route_policy_inventory.py`
  -> **457 passed**.
- No template, JS or route change, so no `check_templates` or browser run.

**Not covered:** MAP-R3 (the TV still gets the GM payload).

<!-- ============ REVIEW (written by the reviewer) ============ -->

## Review

### Codex, 2026-10-16: ACCEPTED

No required fixes. The three scene-frame broadcasts drop player delivery live and on replay;
the GM window keeps its frames. `scene_mood` retains its intended player-facing behavior.
Reviewed together with MAP-R3, which supplies the TV's separate audience.

Independent verification: the R2/R3 tests, SSE security/revocation and replay tests, and route
policy inventory passed: **62 passed**. All **107 templates parse**, `node --check
static/js/map.js` and `git diff --check` pass. Claude's reported browser checks were reviewed;
Codex did not repeat the browser session. Accepted for publication together with MAP-R3,
with merge gated on every CI job succeeding.
