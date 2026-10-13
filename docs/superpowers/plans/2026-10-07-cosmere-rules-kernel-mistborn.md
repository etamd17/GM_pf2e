# Cosmere Rules Kernel, Audit, and Mistborn Implementation Plan

> **For agentic workers:** this is a backend/data implementation. Do not redesign
> templates or CSS. Read `CLAUDE.md` first. Work test-first and keep source PDFs,
> extracted prose, watermarks, and page images out of Git.

**Goal:** replace the cosmetic Cosmere world switch with immutable, composable,
source-cited rules packages; preserve existing Stormlight behavior while correcting
known gaps; add real Mistborn Era 1/Era 2 mechanics and GM-ready private adventure
manifests; expose stable server APIs for a later UI pass.

**Binding specification:** the user-approved plan in the parent Codex conversation.
This file translates that specification into dependency-ordered implementation tasks.

## Global Constraints

- Keep top-level `system: "cosmere"`. Theme is cosmetic and independent of rules.
- Rules packages are immutable and hash-addressed. Campaigns pin exact package IDs,
  versions, and hashes; upgrades are explicit and audited.
- The server profile is the sole rules authority. Routes are thin wrappers; browser
  code and legacy Foundry data do not independently decide legality.
- Store normalized mechanics, original summaries, source IDs, and page citations
  only. Never commit PDFs, extracted book prose, customer watermarks, or page images.
- Source precedence is official errata, newest official handbook, world guide,
  adventure-specific rule, GM summary, then quickstart. Conflicts remain explicit.
- The supplied Stormlight v1.01 core files are quarantined comparison sources.
  Stormlight audit status stays `provisional` until official current files are bound.
- Automation classes are exactly `calculated`, `tracked`, `reference`, and
  `gm_adjudicated`; no record may silently omit its automation disposition.
- Every state mutation requires `expected_revision`, uses the existing per-PC path
  lock/write workflow, persists before broadcast, and rejects stale revisions.
- Existing campaigns, including Mistborn-themed ones, migrate mechanically to the
  pinned legacy Stormlight package. Never reinterpret a theme as a rules choice.
- Preserve current Cosmere routes as compatibility facades until the UI phase.
- No emojis. Windows-readable tests use `encoding="utf-8"` for repository files.
- Do not commit or push unless the user explicitly authorizes it. Reviews use
  cumulative working-tree packages against the branch base.
- Baseline: 401 Cosmere tests passed on 2026-10-07 with `TMP` and `TEMP` set to a
  writable external directory. Run focused RED/GREEN tests per task and the 59-file
  Cosmere suite after integration changes. Run the standard non-browser CI suite at
  final verification; browser/UI work is deferred.

---

## Task 1: Source inventory, schemas, immutable packages, and compiler

**Files:** create `systems/cosmere/rules/` for schemas, hashing, registry, profiles,
and package loading; create `data/cosmere/rules/` for normalized manifests; create a
source-inventory/compile CLI under `tools/`; add focused tests under
`tests/cosmere_rules/`.

**Required behavior:**

- Define validated immutable records for `SourceArtifact`, `SourceRef`,
  `RuleRecord`, `ContentPack`, `CosmereRulesBinding`, `AdvancementEvent`,
  `EffectInstance`, and `AdventureManifest`.
- Validate stable namespaced IDs, semantic versions, SHA-256 strings, source/page
  references, pack dependencies, world/era scopes, spoiler scope, and automation
  status. Reject duplicate IDs, unknown dependencies, hash mismatch, missing
  citations, and adventure records that attempt to override core rules.
- Package hashes are computed from canonical UTF-8 JSON with stable key ordering and
  exclude the package's own hash field. Registry loading fails closed.
- Register all 13 supplied artifacts by product ID, basename, edition, hash, byte
  count, page count, authority tier, extraction class, spoiler scope, and rights
  state. Absolute local paths are runtime input only and never serialized.
- Use these observed artifact facts verbatim (edition `unverified` is intentional
  where no reliable edition marker was observed):

  | Product ID | Basename | Edition | Bytes | Pages | SHA-256 | Rights/status |
  |---|---|---:|---:|---:|---|---|
  | `mistborn.handbook` | `MB001_Mistborn_Handbook_digital.pdf` | `v1.02` | 247547589 | 410 | `1594e01b2bd09623128114072e4b8d279787760eb16550782fbb5b54990cce13` | private |
  | `mistborn.world-guide` | `MB002_Mistborn_World_Guide_digital.pdf` | `v1.02` | 172818796 | 290 | `a6f687781d6da04ebe2f56842cfc6d122a711237c3ce900e8c66edcff0649b22` | private |
  | `mistborn.legacy.maps-tokens` | `MB003_Mistborn_Legacy_battlemaps_and_tokens.pdf` | `v1.02` | 94376824 | 82 | `b1634bdee96b9936f0ccaba40a87e5e75dc096540c8e714a50d6ef8cf2288435` | private |
  | `mistborn.legacy` | `MB003_Mistborn_Legacy_digital.pdf` | `v1.02` | 117857494 | 270 | `a6f6a5a2b44de0e5778d412745aa50b489a96f8bd15edd48a8f98a305ba5fd63` | private |
  | `mistborn.welcome-to-scadrial` | `MB004_Welcome_to_Scadrial_digital.pdf` | `v1.02` | 14893686 | 28 | `be79276507fcd6a101e63983faea152675a8b70bc3e79c99418de020ef0953a9` | private |
  | `mistborn.legacy.gm-tools` | `MB017_Mistborn_Legacy_GM_Tools.pdf` | `v1.02` | 7371551 | 111 | `85ec63a3f75e19943a2a1195c91491ebac7d7a64c48f89732c91691f3ba90159` | private |
  | `stormlight.handbook` | `Cosmere RPG - Stormlight Handbook - Rulebook (Brandon Sanderson) (z-library.sk, 1lib.sk, z-lib.sk).pdf` | `v1.01` | 205426386 | 392 | `8068ac3fee2c9da0f8d2e42d5e97cc95cb1d74cbdc10e25d2b2c30669f3a52ed` | quarantined |
  | `stormlight.world-guide` | `Cosmere RPG - Stormlight World Guide - Lorebook (Brandon Sanderson) (z-library.sk, 1lib.sk, z-lib.sk).pdf` | `v1.01` | 213575280 | 280 | `5a31183807b7369f9128f9b73bca70c344e795740e64652643d4ee64eac49431` | quarantined |
  | `stormlight.stonewalkers.gm-tools` | `Stonewalkers GM Tools.pdf` | `unverified` | 620286 | 41 | `8537d65ebefe761ce562798969743acb64c291d3bfccc0e6063b0655f5d0f3ec` | private |
  | `stormlight.stonewalkers.handouts` | `Stonewalkers Handouts.pdf` | `unverified` | 55805300 | 40 | `842266e8c5d1a82d87fe51af2f1fe31185eb66b192d54637c114b8aea8e999af` | private |
  | `stormlight.stonewalkers.maps` | `Stonewalkers Maps.pdf` | `unverified` | 178500263 | 55 | `53e4c8d9d1adf20af4b8b43474b6d833fcb27fd2d071b0c25e22eaa8bea464e9` | private |
  | `stormlight.stonewalkers` | `Stonewalkers.pdf` | `v1.02` | 68757827 | 173 | `c89bb9a43c2e408562abad17223d38448b81b0b39593b1d30f8fc0614b1ef021` | private |
  | `stormlight.first-step` | `The First Step.pdf` | `v1.02` | 13958137 | 44 | `7f5741dc116ba4f0be3ff3a6bcafa335622806b617929aa429a8e009ce850b5d` | private |
- Mark the two supplied Stormlight core artifacts `quarantined`; all v1.02 Mistborn
  and purchased adventure/support artifacts are private sources.
- The CLI accepts an explicit artifact-ID-to-path mapping, verifies metadata and
  hashes, emits a private extraction inventory outside tracked data, and compiles
  only normalized authoring JSON. It never emits raw PDF text into the repository.
- Seed packages: universal Cosmere core, legacy Stormlight compatibility,
  Mistborn Era 1, Mistborn Era 2, Stonewalkers, The First Step, Welcome to Scadrial,
  and Mistborn Legacy. Empty content sections are allowed initially but every pack
  must have identity, dependency, provenance, and audit status.

**Tests first:** schema rejection cases, canonical hash determinism, duplicate and
dependency failures, citation completeness, quarantine status, no absolute paths in
compiled output, all artifact hashes/page counts, and a compile-load round trip.

---

## Task 2: Rules-profile boundary, bindings, and Stormlight compatibility

**Files:** extend the rules kernel/profile registry; adapt `systems/cosmere/build.py`,
`systems/cosmere/__init__.py`, and the smallest necessary route/service seams; add
profile and compatibility tests.

**Required behavior:**

- Add the `CosmereRulesProfile` boundary with `catalog`, `level_grants`,
  `validate_build`, `project_actor`, `normalize_runtime_state`, `apply_action`,
  `encounter_budget`, and `tracker_block`.
- `CosmereBuild` remains the public compatibility facade and dispatches through the
  active binding. Existing unbound documents resolve only to the pinned legacy
  Stormlight package.
- Separate `cosmere_theme` from `rules_binding`; keep reading `cosmere_world` only as
  a legacy cosmetic input. Add binding normalization for core package, enabled pack
  IDs/hashes, default world, and allowed eras.
- Move world-specific skills, paths, resources, and catalogs behind the profile
  boundary without changing valid Stormlight derived results.
- Add `GET /api/cosmere/rules/catalog` returning the normalized binding, package
  identities, filtered catalogs, automation status, and source references.
- Existing builder preview behavior remains compatible while including package
  identity, eligibility, and blocked reasons.

**Tests first:** legacy Stormlight default, Mistborn theme remaining Stormlight rules,
hash mismatch rejection, pack/world/era filtering, existing Stormlight golden build
vectors, catalog route authorization and payload, and absence of PF2e mechanics.

---

## Task 3: Transactional advancement, revisions, migration, and export

**Files:** create a Cosmere advancement service; integrate persistence through the
existing PC workflow and path locks; add migration/dry-run support and API routes;
add advancement and migration tests.

**Required behavior:**

- Character documents gain `schema_version`, monotonic `revision`, normalized pinned
  binding, origin world, optional era, build choices, derived snapshot, runtime state,
  and append-only advancement events.
- Preview and commit validate exactly one level, prior revision, pinned package,
  budgets, required grants, prerequisites, era availability, path exclusivity, and
  unused completed-goal grants. Underspending required grants blocks commit.
- Revert applies the inverse of the most recent advancement event and creates an
  auditable revision; it is not an arbitrary snapshot restore.
- Expose `POST /api/cosmere/advancement/preview`, `/commit`, and `/revert`.
- Add standalone Cosmere JSON export/import preserving bindings, events, runtime
  resources, and revisions.
- Migration dry-run reports every change. Apply mode performs recoverable backup,
  atomic per-document rewrite, read-back validation, and idempotent reruns.

**Tests first:** levels 1-20 and 21+ grants, missing-grant rejection, stale revision,
replay prevention, goal consumption, revert, import/export round trip, idempotent
migration, rollback on read-back failure, and legacy Mistborn theme preservation.

---

## Task 4: Effect instances, structured actions, and combat correctness

**Files:** add Cosmere effect/action services; adapt actor ingestion and the smallest
combat/tracker mutation seams; add focused effect, action, and role tests.

**Required behavior:**

- Replace boolean-only condition mutation with `EffectInstance` storage while
  projecting legacy condition fields for existing templates and clients.
- Support source, target, parameters, selected attribute, stacking identity,
  duration unit, remaining duration, trigger, refresh policy, and independent expiry.
- Preserve structured adversary activation, cost, test, damage, target, resource,
  opportunity/complication, and use limits from ingested action records.
- Implement ordinary, Minion, and Boss defeat behavior from the active profile.
  Ordinary adversaries do not use the PC injury spiral unless explicitly opted in.
- Add one action mutation path through
  `POST /api/cosmere/pc/<id>/actions/<action-id>` with revision checking, persistence,
  live-combat synchronization, encounter autosave, then scoped SSE broadcast.
- Audit and test fast/slow turns, Plot Die, Deflect, injuries, rest, action costs,
  restrictions for severe conditions, and round/scene expiry.

**Tests first:** multiple Afflicted instances, multiple Enhanced attributes, named
stacking/refresh, each duration unit, structured action preservation, ordinary/
Minion/Boss defeat, stale/replayed action requests, restart persistence, and ordered
tracker/autosave/SSE updates.

---

## Task 5: Shared GM data services

**Files:** add neutral Cosmere compendium, random-table, encounter-budget, party
capability, and custom-adversary services; add read-only/mutation API routes and tests.

**Required behavior:**

- Implement a generic seeded random-table registry with weighted and nested results,
  cycle detection, world/era/pack filters, source citations, roll history, and
  campaign-persisted pins. Expose `POST /api/cosmere/tables/<id>/roll`.
- Add read-only compendium search/detail endpoints over the active package catalog.
- Add profile-provided encounter budgets and staging payloads without PF2e XP logic.
- Add a system-neutral party capability projection and standalone Cosmere export.
- Define richer custom adversary contracts including role, defenses, resources,
  structured actions, traits, equipment, and edit/delete revision handling.
- Expose conversation and endeavor state contracts without creating UI screens.

**Tests first:** deterministic seeded rolls, nested tables and cycle rejection,
filtering and persisted pins, compendium spoiler/auth boundaries, encounter budget
vectors, party projection, and custom-adversary revision conflicts.

---

## Task 6: Mistborn Era 1 and Era 2 profile

**Files:** create normalized Mistborn rule data and profile implementation; extend
character/runtime schemas only through shared interfaces; add Mistborn unit and API
tests.

**Required behavior:**

- Support exactly one heroic path and at most one Metalborn path. Misting works in
  both eras; Mistborn and Feruchemist are Era 1; Ferring and Twinborn are Era 2.
- Allomancy is governed by Willpower. Its Investiture maximum is
  `2 + max(Awareness, Presence)`, starts full unless Surprised, and tracks Depleted
  independently per metal. Vials and rare-metal access are explicit resources.
- Feruchemy is governed by Intellect. It has no Investiture pool; each metal has one
  shared charge pool with maximum `2 + Feruchemy rank`, and the same metal cannot be
  stored and tapped simultaneously.
- Metallic Art rank scaling is: untrained limit 1/no die/10 ft; rank 1 d4/20 ft;
  rank 2 d6/40 ft; rank 3 d8/80 ft; rank 4 d10/160 ft; rank 5 d12/320 ft; rank 6+
  d20/640 ft.
- Enforce goal-gated nascent and full-potential unlocks, access to the relevant
  metal, era/package availability, and server-side prerequisite checks.
- Implement all deterministic Metallic Art actions/effects from normalized records;
  contextual effects are tracked or GM-adjudicated explicitly. Hemalurgy is reward
  content rather than a selectable character path.
- Add Mistborn equipment, currency, rewards, adversaries, encounter data, tables,
  and references with source citations and automation dispositions.

**Tests first:** path/era matrix, governing attributes, investment budgets, all rank
scaling literals, independent depletion, vial restoration, charge clamping,
Store/Tap exclusion, goal unlocks and replay, active durations across all scene types,
wrong-pack rejection, and state normalization when a Metalborn path changes.

---

## Task 7: GM-ready private adventure packs

**Files:** add normalized manifests and compiler validation for Stonewalkers, The
First Step, Welcome to Scadrial, and Mistborn Legacy; add private asset-import service
and manifest tests. Do not add screens.

**Required behavior:**

- Each pack contains spoiler-scoped scene order, prepared encounters, referenced
  adversaries, rewards, tables, and source-page references using stable IDs.
- Asset manifests store logical IDs, type, expected source artifact/hash/page, and
  private storage reference. They do not contain source binaries or absolute paths.
- Import copies user-owned assets through existing authenticated campaign storage,
  verifies hashes/types, and never exposes GM-only assets to player APIs by default.
- Every scene/encounter reference resolves; core rules cannot be overridden by an
  adventure pack. Mistborn Legacy supports both eras and tags each character,
  encounter, and scene context appropriately.

**Tests first:** zero orphan references, spoiler filtering, source citations, private
asset authorization, hash/type rejection, no source prose/binaries, cross-era Legacy
resolution, and deterministic manifest compilation.

---

## Task 8: Integration audit, compatibility hardening, and UI-ready freeze

**Files:** integration tests, audit/coverage report, API documentation, and only the
compatibility fixes required by findings. No visual redesign.

**Required behavior:**

- Produce a machine-readable coverage ledger where every normalized record has a
  source citation, automation disposition, review state, and test reference where
  deterministic.
- Verify no hardcoded browser/legacy table silently disagrees with package authority.
- Verify package upgrade preview/commit is explicit, preserves old immutable
  packages, and can roll back to the prior binding.
- Run migration fixtures, multi-campaign isolation, concurrent stale-write tests,
  active tracker synchronization, restart persistence, import/export, and SSE scope.
- Document the stable API/view-model contracts for the later UI workshop.
- Run `tools/check_templates.py` only if compatibility markup changed.
- Final gates: all focused rules tests, all 59 Cosmere test files, standard
  non-browser/non-PostgreSQL CI suite, package compilation/coverage check, and a fresh
  whole-branch review. Report browser and PostgreSQL jobs separately if unavailable.

**Acceptance:** zero uncited records, zero unresolved references, zero silent
automation gaps, deterministic package hashes, all available required tests green,
and an explicit `provisional` Stormlight audit status until official current core
artifacts are supplied.
