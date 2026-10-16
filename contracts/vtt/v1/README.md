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
