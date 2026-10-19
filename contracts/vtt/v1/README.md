# Canonical VTT wire schemas, batch 1

Source batch for accepted VTT-ADR-0002 and schema amendments S1-S4 in handoffs 015/016.
It publishes contract data and validation tooling. No Flask route, Socket.IO server, or
command handler is registered by this batch.

## Included shapes

`registry.json` maps schema keys to files, events to audience-specific validators, and surfaces
to generation exports. `schema-ready` describes an available shape, not a running handler.
Reserved events fail closed. Select the schema from the authenticated principal's audience,
never from a caller-supplied role.

- `gm` and `table`: separate snapshot/patch unions. Snapshots have sequence zero, campaign/scene
  context, principal, capabilities, and complete projected entities. Patches have sequence >=1,
  complete upserted replacements and explicit removed references. Only GM patches permit
  `originCommandId`; a table cannot originate commands.
- Scene, token, and public light entities have bounded renderer-facing fields. GM variants add
  prep/publication, hidden/locked/controller data, private lights, and wall/door geometry.
  Table variants contain none of those additions. Tokens contain actor references, never HP
  or conditions. The spike's token HP is not adopted.
- `gm-subscribe` and `table-subscribe`: separate principal-scoped roots. A table cannot choose
  campaign/scene or masquerade as a GM. `subscribe` is the aggregate type union, which must
  not be used as the table's runtime validator.
- `acknowledgement`, `resync-required`, and `bootstrap-rejection`: closed outcomes and all
  accepted rejection/control reasons, with no private scene/entity/grant details. The server
  returns its v1 stable bootstrap envelope when rejecting an unsupported requested version.

Commands, transients, whispers, deferred audience projections, combatants, rolls, and plans
remain explicitly reserved for later reviewed batches. No permissive placeholder schema exists.
This is a basis for client session/generation work, not completion of every ADR release gate.

## Source, decoding, and fixture rules

Use draft-07, closed complete objects, required const tags, bounded values, and local references.
All integers have explicit safe bounds. Every application message has schemaVersion 1. The
Python loader rejects unsupported schema keywords, external/missing references, and duplicate
schema identifiers. It preloads a local registry without network retrieval. Validating a shape
does not establish ownership, visibility, publication or combat legality; unique entity keys and the
other frame rules are checked after it (see Corrections).

`fixtures/cases.json` is an ordered array of synthetic cases. Each case has unique `id`, `schema`,
`valid`, and exactly one of inline `message` or `raw`. Raw text carries invalid JSON, duplicate
decoded keys, and overflow that cannot be expressed as a normal parsed fixture. Expected validity
applies after strict decoding/boundary checks and then schema validation.

S1: Claude's browser generator must rewrite only the reviewed Ajv ucs2length helper to its ESM
shim and fail on any unknown or remaining require(). The reference Node runner here compiles
Ajv only for offline agreement tests; it does not replace the browser generator.

S2: `vtt.protocol.StrictJSON` rejects duplicate decoded keys, non-standard/non-finite numbers,
unsafe integers, excessive bytes/depth, and non-JSON values, including outgoing values. Configure
the VTT transport with `socketio.Server(json=StrictJSON, ...)` (or the Flask-SocketIO equivalent)
and use the same boundary in REST adapters when those services land. No endpoint is wired yet.
The client raw-text adapter must also reject duplicates: JSON.parse alone silently loses them.
The Node agreement runner includes a strict raw-case decoder.

S3: `.gitattributes` pins LF for contracts/vtt/** and generated protocol sources. The schema
digest hashes sorted registry.json and schemas/*.schema.json, each as relative POSIX path, NUL,
normalized UTF-8/LF bytes, NUL. Normalize both CRLF and bare CR. Generated-artifact digests must
also include pinned generator versions/options. Fixtures are tested separately from this digest.

S4: lengths count Unicode code points. Astral musical-symbol fixtures cover exactly 80,
beyond 80, mixed strings, and escaped surrogate pairs. Keep Ajv's Unicode semantics enabled.

## Corrections (M0, Claude, 2026-10-16; handoff 017 findings, ownership per handoff 018)

S5: no schema uses `uniqueItems`, and the loader refuses the keyword. Ajv compiles object uniqueness
to a runtime helper S1 forbids. It was quadratic: 886 ms against 4.9 ms for a 2,048-token table
snapshot. It also caught only byte-identical duplicates. Identity is `(kind, id)` (ADR-0002 section 4)
and is checked in code, at both boundaries, after schema validation. The Python rules are in
`vtt/protocol/semantics.py`, the reference Node copy in `tools/vtt/semantics.mjs`, and the client's in
`frontend/vtt/src/protocol/semantics.ts`. A frame is invalid when:
- a snapshot's `entities`, a patch's `upserted` or a patch's `removed` repeats a `(kind, id)`;
- a patch upserts and removes the same key;
- a snapshot carries more than one `scene` entity, or a scene entity that is not `sceneId`'s;
- `sceneId: null` comes with any entities;
- a GM `controllerIds` or `capabilities` list repeats a value;
- a GM wall has zero length.

The server never sends such a frame. A client that receives one treats it as lost state: it does
not apply the frame, retires the subscription, and resubscribes with `state-lost`, keeping the last
good projection on screen.

Byte cap: the 2 MiB frame limit governs. The schema caps (2,048 table entities, 4,096 GM entities,
64 controllers per token) allow a GM frame larger than 2 MiB. `StrictJSON.dumps` refuses to send
one, and the client decoder refuses to read one. Keeping a scene's GM projection under the limit
is a write-time rule for the command layer (M2): a command that would push it over is rejected
with `validation-failed`.

Display text: `Label` and the scene `name` exclude C0/C1 controls, bidi overrides and isolates
(U+202A-202E, U+2066-2069) and unpaired surrogates. Astral characters stay legal. Python's `str`
and Ajv's unicode regexps both see one code point for a surrogate pair, and a raw-text fixture pins
the lone-surrogate case in both runtimes.

`vtt.resync_required` now names the subscription it ends (`subscriptionId`, required). The server
assigns subscription IDs, so a client learns a new one only from its snapshot. The client ignores
a resync that names a subscription it has already retired, or that names another subscription
while it has a live one and no subscribe in flight. A late resync from a fan-out race therefore
cannot tear down a fresh subscription.

Lint: every numeric schema needs `minimum` and `maximum`; every string needs `maxLength` unless it
has `enum` or `const`; every array needs `maxItems`.

Permanent rule: the bootstrap-rejection envelope and the `version-changed` resync stay
`schemaVersion: 1` in every future server. That is how a v1 client learns it is too old.

Token `x`/`y` are the token's centre, in scene pixels.

## Run

Create the isolated contract-test environment in the Codex platform worktree:

```text
uv venv venv/vtt-contracts --python 3.11
uv pip install --python venv/vtt-contracts/Scripts/python.exe --require-hashes -r requirements-vtt-contracts.txt
venv/vtt-contracts/Scripts/python.exe -m pytest -q tests/vtt/contracts
venv/vtt-contracts/Scripts/python.exe -m vtt.protocol.check
node tools/vtt/check-contracts.mjs --client-root <absolute frontend/vtt path> --typecheck
```

On Linux use venv/vtt-contracts/bin/python. The Node runner resolves Claude's pinned development
packages from the supplied client path without changing that checkout. Both runners offer --json
for case results and the identical schema digest. Type generation is proved in a disposable
directory; the real generator and committed browser outputs remain Claude's work.

These checks do not read or change source scenes, assets, live campaigns, or credentials.

## Commands, batch commands-1 (M2, Claude, 2026-10-16)

Additive. `vtt.command` is `schema-ready` for the GM surface only, validated by `command`
(`schemas/command.schema.json`); a table has no command schema and is disconnected if it sends one.
Transients and whispers stay reserved. No existing schema or fixture changed.

- The envelope is closed: `schemaVersion`, `commandId` (`OpaqueToken`), `campaignId`, `sceneId`,
  `type`, `expected` and `payload`, as a `oneOf` over the twelve types in `commandVariants`:
  `scene.create`, `scene.update`, `table.show`, `table.clear`, `token.create`, `token.update`,
  `token.move`, `light.create`, `light.update`, `wall.create`, `wall.update`, `entity.delete`.
- `expected` lists `{kind, id, version}` preconditions. A create carries none (`maxItems: 0`); every
  other type carries at most one.
- Ids for creates are chosen by the client, so a retried create is idempotent under its receipt and
  the client knows the id without a new acknowledgement field. The server keys scenes and entities
  per campaign, so the same id in two campaigns is two objects and reveals nothing.
- `scene.update` fields are all optional; an empty update is a valid no-op that still completes.
- Rules the schema cannot express (all three semantic runtimes; the client copy lands with M4):
  - `expected` repeats no `(kind, id)`;
  - an update, move or delete names its target's expected version, and so do `table.show` and
    `table.clear` (`("scene", sceneId)`);
  - `controllerIds` repeat nothing;
  - a created wall, or an updated one naming all four ends, is not zero-length. A partial update
    that makes a wall zero-length is refused by the server with `validation-failed`.
- The acknowledgement schema is unchanged. Rejection codes and their receipt behaviour are
  ADR-0002 section 8; the server's order of checks is documented in `vtt/commands/service.py`.
- The 2 MiB rule from M0 is enforced at write time: a command that would make the scene's GM
  snapshot exceed the frame limit or the 4,096 GM / 2,048 table entity caps is refused with
  `validation-failed` and changes nothing.

## Fog of war, batch parity-1 (P1, Claude, 2026-10-17)

Additive for clients that ignore unknown entity kinds; a v1 client built
before this batch rejects a frame carrying `fog`, so the GM and table clients
ship with it. Digest `607148b1...`.

- **Entity `fog`** (`common.schema.json#/definitions/FogState`), one per scene,
  id `fog`, in both the GM and table projections. It has these fields:
  - `enabled`;
  - `cols` and `rows`: the scene's grid, `ceil(width/gridSize)` by
    `ceil(height/gridSize)`;
  - `revealed`: a base64url bitset, row-major, least significant bit first.
    A set bit is a revealed cell.

  The server sends it even before any fog command (fog off, nothing
  revealed). A scene with more than 262,144 cells has no fog entity.
- **Commands:**
  - `fog.update {enabled}`, against the fog version.
  - `fog.reset {}`, against the fog version.
  - `fog.reveal {mode: reveal|hide, cells: [[col,row], ...]}`, with 1 to
    20,000 cells. It carries no precondition (`expected` has `maxItems: 0`).
    It is a set operation, so concurrent reveals compose and a retry changes
    nothing. The server refuses a cell outside the grid (`validation-failed`).
- **Semantic rules**, in all three runtimes:
  - a fog entity's `cols * rows` is at most 262,144;
  - its `revealed` is the canonical unpadded base64url of exactly
    `ceil(cols*rows/8)` bytes. Decoding and re-encoding gives the same string
    back, so lenient decoders cannot disagree;
  - the bits past `cols*rows` are zero;
  - `fog.update` and `fog.reset` name `("fog", "fog")` in `expected`.
- **Server rules the schema does not carry:**
  - The table projection leaves out every token and light whose centre cell
    is not revealed while fog is on.
  - A `scene.update` that changes the grid is refused (`rule-violation`)
    while any cell is revealed.
  - A scene too large for fog answers `rule-violation` to any fog command.

## Area templates and terrain, batch parity-2 (P2, Claude, 2026-10-17)

Digest `32bd0faa...`.

- **Entity `template`**: `GmTemplate` for the GM, `TableTemplate` for the table, which has no
  `hidden`. Fields:
  - `shape`: burst, emanation, cone or line;
  - `x`, `y`: the anchor, in scene px;
  - `aimX`, `aimY`: the aim, used by cone and line;
  - `size`: in squares, 1 to 60;
  - `color`;
  - `hidden`: GM only.

  Geometry follows /map: Euclidean distance, a 90-degree cone, a line one square wide. Up to
  512 per scene.
- **Commands for templates:**
  - `template.create`, with a client id and no precondition;
  - `template.update`, which changes the anchor, aim, size, colour or hidden flag, against its
    version. The shape is fixed.
  - `entity.delete` accepts `template`.
- **Entity `terrain`** (`TerrainState`), one per scene with id `terrain`, sized to the scene grid
  like fog:
  - `cells` is a base64url run of 4-bit kinds, two cells per byte, low nibble first.
  - Kinds: 0 none, 1 lava, 2 water, 3 poison, 4 blood. 5 to 15 are reserved.
  - It is cosmetic, as on /map.
- **Commands for terrain:**
  - `terrain.paint {cells: [[col,row,kind], ...]}`, up to 20,000 cells, with no precondition.
    Each cell carries its kind, so the inverse is the same command with the previous kinds.
  - `terrain.reset {}`, against the terrain version.
- **Semantic rules**, in all three runtimes:
  - a terrain entity has at most 262,144 cells;
  - `cells` is the canonical encoding of exactly `ceil(cols*rows/2)` bytes;
  - every nibble is at most 4, and the spare nibble is zero;
  - every `terrain.paint` cell's kind is at most 4. The Python boundary does not accept tuple
    `items`, so this is a rule rather than schema;
  - `template.update` names its template;
  - `terrain.reset` names `("terrain", "terrain")`.
- **Server rules:**
  - The table projection leaves out a hidden template, or one whose anchor cell is under fog.
  - The table's terrain is masked by fog: unrevealed cells are sent as 0.
  - A grid change is refused while any terrain is painted.

## Live tracker actors, batch parity-3 (P3, Claude, 2026-10-17)

Digest `610eed46...`.

- **Entity `actor`** (`GmActor`), GM only. It is a tracker combatant (`cmb_<instance id>`) or a
  party character (`chr_<character id>`). Fields:
  - `name`, `pc`, `hp`, `maxHp`;
  - `conditions`: up to 24 `{name, value}`, where value is null for a plain flag;
  - `active`: whose turn it is;
  - `hidden`: hidden in the tracker;
  - `status`: "", Wounded or Dead, from the tracker's player policy.

  Actors are campaign-wide: every GM snapshot carries all of them, up to 64. They are derived
  from the tracker, and no command writes them.
- **`TableToken.live`** (`TableLive` or null): what the table may know of the linked actor.
  - `hp`/`maxHp` are exact for a player character and null otherwise.
  - `status` is Wounded or Dead for a non-PC, and null otherwise.
  - Also `conditions` and `active`.

  The schema leaves the field optional, but the server always sends it: null when the token is
  unlinked or its actor is absent.
- **Server rules:**
  - A token linked to an actor that is hidden in the tracker is not sent to the table.
  - An `actors` outbox event, which has no scene, rechecks the named actors for the GM, and
    the tokens linked to them for the table.

## Ping and ruler transients, batch beta-1 (Claude, 2026-10-18)

Digest `5dbce750...`.

- **`vtt.transient`, sent by the GM only,** uses `TransientSend`: `{schemaVersion, subscriptionId,
  transient}`. A table that sends one is disconnected.
- **`vtt.transient_relay`, from the server to GM and table,** uses `TransientRelay`:
  `{schemaVersion, subscriptionId, key, ttlMs, transient}`.
- **`Transient`** is a closed set:
  - `ping {x, y}`;
  - `ruler {rule, points[2..8]}`;
  - `ruler-clear`.

  A ruler's `rule` is `uniform` (every square 5 ft, diagonals included: Cosmere, D&D 5e) or
  `alternating` (5/10 ft diagonals: PF2e). The GM chooses it from the campaign's system, so the
  TV counts as the GM does.
- **Server rules:**
  - A transient is relayed only for the sender's current live GM subscription and its scene.
  - Coordinates must lie within the scene.
  - There is a rate limit per sender.
  - It is never echoed to the sender.
  - **GMs** on the same scene receive it.
  - **The table** receives it only when that scene is on the TV and no point, and no point sampled
    every half square along a ruler, lies under fog.
  - `key` is an opaque latest-wins key per sender and kind: a ruler and its clear share one key.
  - The TTL is 3 s for a ping and 10 s for a ruler.
  - **Nothing is stored, replayed or acknowledged.** Clients clear all transients on every
    snapshot.
- **`table.show`** (commands-1) already publishes and points the table in one transaction, so the
  Editing and On TV split (U-3) needs no new command.

## Creature placement hints, batch creatures-1 (U-4, Claude, 2026-10-19)

Digest `dd14bd57...`.

- **`GmActor` gains `size`** (number, 0.25-10): the token size in squares for placing the creature,
  from its creature size. It falls back to 1.
- **`GmActor` gains `sheetUrl`** (a string or null): **a same-origin path only**. No scheme, no
  `//`, no backslash; at most 256 characters.
  - It is resolved by the server: a PC's sheet, or `/tracker` for a monster.
  - The page opens it as the GM's link to that creature's sheet.
  - It is GM-only, like the rest of `actor`, and never reaches the table.
- Both fields are optional in the schema and always sent by the server. The mirror takes them from
  the same token candidates `/map` offers, on detached copies, so `/map`'s data is unchanged. A
  failure building them leaves them out without stopping the live mirror.

## Turn controls, batch turns-1 (E-1, Claude, 2026-10-19)

- **`GmActor` gains `round`** (an integer 0-100000, or null): the encounter's round on a tracker
  combatant (`cmb_`), null on a party character (`chr_`). Optional in the schema, always sent.
- **`GmActor` gains `turnToken`** (a string `[A-Za-z0-9-]{1,64}`, or null): the tracker's turn
  token on a combatant, null on a party character. Opaque and **never reused**: a boot nonce plus a
  revision that moves on every change of the encounter's combatant objects, turn index or round.
  So Next then Previous, or reloading a saved encounter with the same ids and round, gives a new
  token. Optional in the schema, always sent. (Review 095.)
- The GM page changes turns with `POST /vtt/api/turn` (campaign GM, CSRF), body
  `{campaignId, direction: "next"|"prev", expectedActorId, expectedRound, turnToken}`: the turn it
  showed. The server compares the token, actor and round under the dispatch lock, then runs the
  tracker's own next/previous turn, so every start- and end-of-turn rule runs as it does from
  `/tracker`. An accepted request runs at most once. It answers `{outcome, ...}`:
  - `ok` (200) with the new `actorId`, `round` and `turnToken`;
  - `stale` (409) with the current values, when the page was behind: a duplicate click, a replay
    after Next and Previous, or a request from before an encounter reload;
  - `not-live` (409) when the campaign is not the loaded one (it is never activated);
  - `no-encounter` (409); `unsupported` (422, a system without an integrated tracker: D&D 5e);
  - `blocked` (422) when the tracker refused; nothing moved.
- The result reaches every page through the live actor mirror, never from the response.
- "Previous turn" moves the marker back; it does not undo effects the tracker already applied.

## Conditions from the map (E-2, Claude, 2026-10-19)

No schema change: the conditions already reach the GM page in `GmActor.conditions`.

- The GM page changes one with `POST /vtt/api/condition` (campaign GM, CSRF), body
  `{campaignId, actorId: "cmb_...", condition, operation: "add"|"decrease"|"remove",
  expectedValue, rounds?}`. `expectedValue` is what the page showed: 0 when absent, 1 for a flag,
  else the value. The server runs the tracker's own engine for the campaign's system:
  - **PF2e**: add raises a valued condition or sets a flag, decrease lowers it, remove clears it;
    `rounds` (0-100) sets the tracker's expiry timer on add;
  - **Cosmere**: add and remove only, with no timer from here.
- Outcomes: `ok` (200, the new `value`), `stale` (409, the current `value`; a duplicate click is
  this), `not-live` (409, never activated), `not-in-encounter` (409), `unsupported` (422, D&D 5e),
  `invalid` (422: a condition outside the system's list, a combatant of another system, or an
  operation the system does not have), `blocked` (422, the tracker refused).
- The result reaches every page through the live actor mirror.
