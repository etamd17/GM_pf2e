# PE-03: Obsidian v0.9 reuse review

Reviewed 2026-11-08. **Port selected behavior with regression fixes; do not merge
the old branch wholesale.** The required suites pass, but synthetic failure
probes reproduce six candidate issues, including an inherited collision that
v0.9 makes worse. An additional inherited server persistence failure belongs to
the planned state/receipt work. No product code was changed or installed.

## Baselines and scope

| Source | Exact revision |
|---|---|
| Current main and review branch base | `d08085b0fd003b7c80e7ca152aa212c79419e445` |
| `feat/obsidian-session-operations-v0.9` | `1d0b895e6992dd46eb5172a8026f2f81a6b2e209` |
| Merge base | `cbefabe7761d98eea892560ed2d56c017922bd9f` |

The review branch is `codex/pe-03-obsidian-reuse-review`, worktree
`GM_pf2e_pe03`. Separate detached, initially clean checkouts
`GM_pf2e_pe03_main` and `GM_pf2e_pe03_candidate` supplied the tests.
Their tracked/untracked Git status remained clean after testing.
The original external candidate worktree was neither repaired nor inspected for
uncommitted content. No vault, existing credential/token files, live campaign,
or production session was read. The installed plugin hash remains unverified.
Synthetic blueprint tests create and read only isolated test-token files.

Commands, with `origin/main` at the main SHA above:

```text
git rev-list --left-right --count origin/main...feat/obsidian-session-operations-v0.9
255  5
git log --oneline origin/main..feat/obsidian-session-operations-v0.9
1d0b895e fix: make session evidence recovery durable
fd223136 fix: guard canonical session provenance
a991e379 Merge remote-tracking branch 'origin/main' into feat/obsidian-session-operations-v0.9
5dd705e8 feat: bind Obsidian sessions to canonical packages
4f100aa9 feat: harden Obsidian encounter console
git diff --stat origin/main...feat/obsidian-session-operations-v0.9
 app.py                                 |  376
 core/obsidian_sync.py                   |   48
 core/route_policy.py                    |    1
 services/obsidian_sync.py               |  119
 tests/test_obsidian_operations_v2.py     |  266
 tests/test_obsidian_plugin_behavior.py   |  785
 tests/test_obsidian_plugin_contract.py   |   68
 tests/test_obsidian_sync.py              |  229
 tests/test_route_policy_inventory.py    |    1
 tools/obsidian-plugin/README.md          |  128
 tools/obsidian-plugin/main.js            | 1698
 tools/obsidian-plugin/manifest.json      |    2
 tools/obsidian-plugin/styles.css        |   34
13 files changed, 3439 insertions(+), 316 deletions(-)
```

Stage 0 counted 253 main-only commits at `7aeb9549`; the two additional commits
are the Stage 0 documentation commit and its merge. Candidate head, merge base,
five candidate-only commits, and aggregate product diff are unchanged.
`git merge-tree --write-tree --name-only origin/main feat/obsidian-session-operations-v0.9`
returned exit 1 with a content conflict in `app.py`. It created no working-tree
merge. Other paths merging textually would not establish semantic compatibility.

## Decisions for every candidate-only commit

| Commit | Decision | Retained behavior and qualification |
|---|---|---|
| `4f100aa9` | Selective port | Portable creature lookup, manifest display names, source-backed hazards and saved-encounter round trips; campaign mismatch guard, typed area HP, initiative controls and capability status. Preserve current-main mechanics and workflow guards. |
| `5dd705e8` | Selective port after fixes | Canonical package discovery, explicit run-sheet/record/closeout bindings, categorized capture, optional server provenance, capabilities and read-only room diagnostics. Fix F1/F4/F5; package completeness must not gate general player access or D&D encounters. |
| `a991e379` | Ancestry only | Historical merge of then-current main. Do not cherry-pick or treat it as an implementation slice. |
| `fd223136` | Fold guards into corresponding ports | Campaign-prefixed identifiers, terminal-status exclusions, collision/containment checks, closeout evidence and lifetime session-ID intent. Use the final migration implementation with F6 corrected, not this intermediate revision alone. |
| `1d0b895e` | Conditional port; replace server durability mechanism later | Origin/campaign queues, generation guards, paginated serialized event drain, deduplication, original acknowledged evidence and local retry, revision-bound closeout, retryable history scan. Fix F1-F4/F6 before acceptance. Its JSON/JSONL persistence is not PE-14's atomic receipt/outbox contract. |

## Findings that must shape the ports

All source lines below refer to candidate `1d0b895e` unless marked **main**.
The probes use synthetic identities, fake vault/network operations, and isolated
temporary server data. They demonstrate failures, not fixes or live incidents.

### F1 — P1: persisted evidence is not scoped to the connection

`tools/obsidian-plugin/main.js:2645` and `:2688` (`ensureSessionOwnership`) check
only `session_id`; `:2706` (`assertGeneratedOwnership`) preserves that assumption.
At `:2756`, `appendEvent` deduplicates by sequence in the shared session file.
Mirror origin/campaign A's `tle-s001`, event sequence 1, then connect to B with
the same session ID and its own sequence 1. B's snapshot overwrites A's; B's
event is discarded while its cursor advances to 1. `Combat Events.jsonl` retains
only `A-event` beside B's snapshot. Main already mixes/overwrites these paths;
candidate sequence deduplication introduces the additional silent event loss.
P1 prioritizes persistent loss of local session evidence; the reproduction's
affected state is the generated local mirror.

Bind folder ownership, snapshot replacement, legacy adoption, and event identity
to origin + campaign + session. Refuse ambiguous legacy evidence before writing
or advancing a cursor. Test same IDs across campaigns and across origins.
The current legacy-adoption test explicitly accepts evidence without campaign
identity; preserve the recovery intent, not that insufficient acceptance rule.

### F2 — P2: local retry overwrites a newer snapshot

`main.js:1819` (`retryPendingMaterializations`) writes old `command_mirror`
evidence through `persistSnapshot:2763`. With a revision-8 snapshot already saved
and failed revision-4 evidence pending for that same session, retry fetches
revision 8 but writes revision 4/HP 30 over it and clears the pending item.
An unchanged poll leaves the stale file intact (`syncNow:1532` snapshots only
on a model revision change). This is introduced by the recovery implementation.

Guard replacement by connection/session/revision; retain historical acknowledged
evidence separately when needed. Test both same-session stale retry and the
already-covered recovery into a different, older session.

### F3 — P2: retry discards newly queued recovery evidence

`main.js:1810` replaces `pendingMaterializations` using a selection captured
before awaiting `/state`. Start with session A pending, then queue an acknowledged
but locally failed session B closeout while that request waits. After retry,
`[tle-s001, tle-s002]` becomes `[]`: B was neither processed nor retained.
This is introduced by `1d0b895e`.

Remove only the exact successfully processed item/version from the current
queue; preserve concurrent additions and replacements. Test enqueue during the
state fetch and during a materialization, plus a connection switch at each wait.

### F4 — P2: accepted canonical IDs cannot always be materialized

The regex at `main.js:16` has no length cap; `sessionFolder:2585` uses
`safeSegment`'s 100-character truncation. A 101-character canonical ID
(`'a'.repeat(96) + '-s001'`) is accepted by `sessionPackages` when reciprocal
references match the plugin's derived paths and passes `assertSafeSessionStart:2551`, but
`ensureSessionOwnership:2634` refuses it as unsafe/ambiguous. Server session
IDs allow up to 120 characters (`services/obsidian_sync.py:781`). A successful
remote start can therefore leave a local recovery item that cannot succeed.

Validate one lossless ID contract before sending the command; do not silently
truncate identity. Cover 100/101/120 characters and the server's upper boundary.

### F5 — P1: room preflight misses a destructive launch failure

`app.py:24844` (`_obsidian_prepare_room_encounter`) validates roster count/path
but not `elite_weak`. `_obsidian_preflight_room_manifest:25090` reports
`valid:true` for a known creature with `elite_weak:'invalid'`. Launch clears
the encounter and round events (`:24859`) before conversion raises at `:24873`.
Both baselines reproduce the emptied encounter/events; the destructive launch
path is inherited, while the new preflight incorrectly approves the input.
Also, a present `encounter.monsters` object or string is treated as absent and
returns `valid:true` with a warning (`:25086`), rather than a type error.

Reject malformed nested rosters and adjustment values, and finish validating
and constructing the replacement before mutating live state. Add failed-build
tests preserving the old encounter/events. This immediate port requirement is
distinct from PE-61's full revision-bound, atomic roster-plus-effects launch.

### F6 — P2: historical ID protection fails open on backfill failure

`core/obsidian_sync.py:192` leaves `session_id_history_backfilled=False` after
unreadable/malformed history (`:214`). `services/obsidian_sync.py:784` checks
only the incomplete `used_session_ids` list. With a malformed historical log
line preceding a prior start, the real blueprint accepts `legacy-s001` again
with HTTP 200. The new lifetime guard therefore does not cover its own migration
failure path. Main has no equivalent lifetime protection.

Refuse a new session start while uniqueness cannot be established; report a
recoverable history error without marking the backfill complete. Test attempted
start during failure and successful recovery, not only the retry flag itself.

### F7 — inherited architecture: mutation, event and receipt are not atomic

`services/obsidian_sync.py:870` appends the event before `:882` saves runtime and
receipt; `core/obsidian_sync.py:298` derives sequence from that separate runtime.
On both baselines, injected receipt-save failure leaves damage applied
(HP 30 to 23). Refresh state and retry the same command ID: HP falls to 16,
events have sequences `[1,1]`, and `idempotent_replay` is false.
The successful-path idempotency test does not prove crash consistency.

PE-13 repositories and PE-14's atomic state/receipt/event/outbox boundary must
replace this durability model before the new authority is activated. Receipts
are also limited to 250 entries and do not bind a command ID to a request digest.
These are inherited limitations, not newly introduced v0.9 regressions.

## Reuse map and required evidence

Names in the test column are exact functions in the indicated test module.
`B` = `tests/test_obsidian_plugin_behavior.py`, `S` = `tests/test_obsidian_sync.py`,
`O` = `tests/test_obsidian_operations_v2.py`. Keep contract checks in
`tests/test_obsidian_plugin_contract.py` with each client/server slice.

| Behavior / source functions | Existing evidence | Required additions / boundary |
|---|---|---|
| `app.py` creature lookup, `_obsidian_prepare_room_encounter`, `_obsidian_launch_room_encounter`, `_make_hazard_combatant`, encounter serialization | O: `test_room_launch_resolves_portable_manifest_paths_on_windows`, `test_room_launch_honors_manifest_name_without_mutating_library`, `test_manifest_name_survives_saved_encounter_round_trips`, `test_source_backed_hazard_card_and_autosave_round_trip` | F5; preserve current-main campaign-local library lookup, serializer fields and Cosmere phase reset. |
| `main.js` `sessionPackages`, `sessionPackagePayload`, `assertSafeSessionStart`; service `_session_package`, `_closeout_preflight` | B: `test_canonical_campaign_packages_are_grouped_without_guessing_or_reuse`, `test_closeout_preflight_accepts_explicit_none_values`, `test_categorized_capture_carries_room_provenance`; S: `test_canonical_session_package_and_forced_closeout_are_persisted` | F4, optional-package compatibility, prepared/terminal states, no authored-note overwrite. |
| `main.js` `ensureSessionOwnership`, `assertGeneratedOwnership`, generated-file writes | B: `test_local_collisions_are_rejected_before_server_start`, `test_legacy_v08_artifacts_are_detected_but_never_rewritten` | F1 and F4; ambiguous legacy files must stay untouched. |
| `main.js` `acceptCommandResponse`, `rememberPendingMaterialization`, `tryLifecycleMaterialization`, `retryPendingMaterializations` | B: `test_remote_acknowledgement_is_decoupled_from_recoverable_local_mirroring` | F2/F3; restart after failed plugin settings persistence, overlapping retry, same-session newer snapshot. In-memory retention alone is not durability across app restart. |
| `main.js` `captureConnectionContext`, `connectionContextMatches`, `sendCommand`, `retryPending`, connection reset | B: `test_offline_commands_are_connection_scoped_and_switches_only_reset_cache`, `test_connection_switch_invalidates_inflight_state_commands_and_event_cursors`, `test_unscoped_legacy_pending_commands_are_quarantined`, `test_pending_retry_is_bound_to_one_connection_generation` | Extend switches into local-write waits and concurrently changing queues; preserve quarantined legacy commands. |
| `main.js` `pullEvents`, `appendEvent`, snapshot adoption | B: `test_event_mirror_is_complete_idempotent_and_can_adopt_valid_v08_evidence`, `test_a_stale_state_response_cannot_rewind_the_client`, `test_idempotent_replay_is_acknowledged_but_not_adopted` | F1/F2; cursor advances only after durable, correctly scoped writes. Bind to accepted PE-31a snapshot/replay contract. |
| `core/obsidian_sync.py` `load_runtime`, `read_events`; service `start_session` dispatch | S: `test_runtime_migration_backfills_every_historical_session_start`, `test_runtime_history_migration_retries_after_event_read_failure`, `test_canonical_package_requires_stable_campaign_prefixed_id` | F6 and ID limits; migrate into PE-13/14 storage rather than treating JSON history backfill as the final uniqueness constraint. |
| Service `room_preflight`, `_capability_payload`; app `_obsidian_preflight_room_manifest` | S: `test_room_manifest_preflight_is_authenticated_and_read_only`, `test_status_and_state_advertise_the_versioned_capability_contract`; O: `test_room_manifest_preflight_reuses_launch_validation_without_mutation`, `test_room_manifest_preflight_reports_all_independent_errors`, `test_room_manifest_preflight_rejects_falsy_wrong_types` | F5, correct profile/edition, explicit starting effects and changed-state rejection in PE-61. |
| Lost remote acknowledgment and replay | S: `test_command_is_revisioned_idempotent_and_evented`; B: `test_idempotent_replay_is_acknowledged_but_not_adopted`, offline/generation tests above | Existing tests cover replay handling and queued requests, not an integrated dropped-response/crash round trip. Add response-loss after durable commit, receipt-write failure, cache horizon and payload mismatch tests under PE-14/70. |

## Newer main must remain authoritative

- Main `services/obsidian_sync.py:150` and `:273` call `preflight_targets` before
  multi-target HP and undo; `app.py:29378` registers `_workflow_preflight_targets`.
  Candidate lacks these pending-publication availability guards. Retain them
  and test multi-target HP/undo with a blocked-publication target, ensuring no
  earlier target mutates before the operation is rejected.
- Preserve main's route-policy integration dispatch lock and campaign-bound bearer
  checks. The proposed `/room-preflight` is correctly classified as
  `INTEGRATION_TOKEN`; keep its active-campaign check and read-only semantics.
- Main `app.py:28954` clears `COSMERE_PHASE_BOUNDARIES` on room launch. Retain that
  reset, campaign-local monster handling, current save formats and character
  workflow behavior while inserting selected candidate helpers.
- Review the final adapter registrations and route inventory, even where Git
  reports a clean textual merge. Do not replace either server module wholesale.

## Limits and bounded port order

The current v0.9 workflow is a GM narrative-package feature. Its run sheet,
record and closeout requirements can remain within that opt-in workflow; they
are not prerequisites for owning a character, joining Play or running a D&D
encounter. The service still accepts sessions without a package. Preserve that
distinction when exposing capabilities.

PF2e condition vocabulary, rolls, hazard/elite-weak handling, library paths and
process-global encounter state do not provide D&D 2014/2024 parity. Route commands
through each accepted system adapter and explicit campaign rules profile.
Revision drift detection of website changes does not create all-origin events;
player actions, automatic effects, advancement, inventory and economy still
need their command writers migrated. Obsidian mirrors remain read-only outputs.

Proposed subdivisions of PE-60, to be briefed after its existing dependencies
PE-03 and PE-31a are accepted/integrated. These labels are a port order, not
new ready queue tasks; split further to keep each human diff reviewable.

1. **Ownership and identifier contract:** F1/F4, explicit connector identity,
   safe legacy refusal and collision tests; preserve authored notes.
2. **Client queues and local recovery:** F2/F3, generation guards and settings
   failure/restart tests, then paginated drain/deduplication using step 1.
3. **Optional package lifecycle:** bindings, categorized capture and closeout
   checks; F6 against PE-13 repositories/PE-14 receipts, with legacy clients
   tested. Server lifecycle operations use accepted state transactions.
4. **Reusable encounter details:** portable lookup, names, hazards and round trips;
   preserve newer main guards. Correct F5 before exposing the preview capability.
5. **PE-61 preview/launch and PE-63 mirrors:** system-aware roster/effects preview,
   revision-bound atomic launch and all-origin summaries/history, using the
   migrated PF2e/D&D writers and activated PE-31a projection. PE-62 separately
   adds approved advancement/economy/downtime controls.

PE-31 remains inactive server primitives until PE-31a's authority/migration gate;
client recovery is no reason to activate it early. PE-70 failure injection must
include F1-F7 plus cross-client response loss, privacy and replay. None of these
ports, schema changes, installations or activation steps occurred in PE-03.

Note (2026-11-10, PE-01 acceptance): the activation gate is now its own task.
"PE-31a's authority/migration gate" above means PE-24, the per-campaign
activation gate (`file → migrating → db`, migration gate, abort,
`db → exporting → file`). PE-31a is now replay activation only and depends on
PE-24 and PE-31. The other PE-31a mentions in this report are unchanged.

## Verification record

Windows, Node `v24.12.0`, Python `3.11.9`, pytest `9.1.1`. Both baselines used
the existing `GM_pf2e/venv/Scripts/python.exe` interpreter with separate synthetic
`DATA_DIR` directories, JSON backends and no database connection. No dependencies
were changed. These are local existing-environment results, not a fresh lockfile
installation or a full-suite/browser/production certification.

```text
node --version
python --version
python -m pytest -q tests/test_obsidian_sync.py tests/test_obsidian_operations_v2.py tests/test_obsidian_plugin_contract.py tests/test_obsidian_plugin_behavior.py --strict-markers
python -m pytest -q tests/test_route_policy_inventory.py --strict-markers
```

| Baseline | Obsidian suite | Route-policy suite |
|---|---|---|
| Main `d08085b0` | 39 passed, 0 failed/skipped; 125.29 s | 18 passed, 0 failed/skipped; 80.98 s |
| Candidate `1d0b895e` | 74 passed, 0 failed/skipped; 115.35 s | 17 passed, 0 failed/skipped; 80.27 s |

Real plugin class behavior ran under Node; none was skipped. Candidate had no
old-baseline test failure to explain. Passing tests do not cover the reproduced
failures above. Two diagnostic collection-only runs on main also collected all
18 route tests; they are not added to the pass totals.

Local scratch evidence is retained outside the product worktrees at
`output/player-experience-planning/pe03-evidence/` in the parent workspace:
four suite logs, `client-recovery-probes.js`, `session-id-probe.js`,
`server_probes.py`, and synthetic server result JSON. From that workspace:

```text
node output/player-experience-planning/pe03-evidence/client-recovery-probes.js GM_pf2e_pe03_candidate/tools/obsidian-plugin/main.js
node output/player-experience-planning/pe03-evidence/client-recovery-probes.js GM_pf2e_pe03_main/tools/obsidian-plugin/main.js
node output/player-experience-planning/pe03-evidence/session-id-probe.js GM_pf2e_pe03_candidate/tools/obsidian-plugin/main.js
GM_pf2e/venv/Scripts/python.exe output/player-experience-planning/pe03-evidence/server_probes.py GM_pf2e_pe03_candidate output/player-experience-planning/pe03-evidence
GM_pf2e/venv/Scripts/python.exe output/player-experience-planning/pe03-evidence/server_probes.py GM_pf2e_pe03_main output/player-experience-planning/pe03-evidence
```

Probe outputs record observed failures and exit successfully when reproduction
completes; they are not passing product regression tests. The server harness
extracts room helper definitions and drives the real blueprint with the existing
synthetic adapter. It does not import the full application. Future port briefs
must turn these cases into committed regressions; scratch files are not part of
the three-file documentation deliverable.
