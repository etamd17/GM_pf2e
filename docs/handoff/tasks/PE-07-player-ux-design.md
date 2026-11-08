# PE-07: Design the integrated player journeys

- **Owner:** claude
- **Reviewer:** codex for scope/contracts/accessibility; the user selects the visual direction.
- **Status:** ready; documentation and isolated mockups only
- **Branch:** `claude/pe-07-player-ux-design` from the recorded Stage 0 package commit (or current main after this package integrates), after all named dependencies are accepted and integrated. Record the exact starting SHA.
- **Depends on:** none for design; consume PE-02 and state/command contracts as they become available, marking provisional bindings.
- **Context:** [approved plan](../../player-release/implementation-plan.md), [fixed decisions](../../player-release/decisions.md), [design direction](../../design/DESIGN_DIRECTION.md), [existing work](../../player-release/existing-player-work.md), [UI-11](UI-11-pf2e-levelup-and-builder.md).

## Goal

Create reviewable tablet/laptop-first designs for the agreed PF2e and D&D player
experience, plus a compact phone companion. Obtain a visual selection before
product UI implementation, without repeating the completed product discovery.

## Approach

Create `docs/player-release/player-ux-design.md` and self-contained, synthetic
mockups under `docs/player-release/mockups/`. Keep mockups isolated from served
templates and application state. Reuse existing DS tokens, system themes,
accepted character-sheet directions and UI-11 navigation/preview work. Follow
the current plain-CSS/Jinja direction; do not introduce a framework or asset
pipeline prerequisite.

Provide two focused visual alternatives for the main Play layout using the same
content and behavior, then one coherent journey set following the selected
direction. Show personal HP/resources, self-applied conditions/effects,
targeted actions, physical/digital rolls, pending saves/reactions, public
initiative and events together. Detail drawers explain derived totals and
durations without exposing GM-only data. Different systems retain their own
terms and mechanics; include both D&D campaign-edition labels.

Cover My Characters and outside-session access, sheet/build/advancement and
resumable drafts, inventory/containers/equipped effects, accepted transfers,
shop/catalog purchase requests, crafting/downtime, and party-loot reservation
and finalization. Show owner versus GM/quartermaster controls correctly:
quartermasters cannot approve purchases or unlock levels. Include loading,
empty, offline/reconnecting, stale/conflict, insufficient-resource, pending
approval and permission-revoked states. Offline is a status, not an offline
editing promise. Hidden enemies and unrevealed effects are absent, not blurred.

Use a representative 1440x900 laptop, 1024x768 tablet and 390x844 phone view.
Provide keyboard/focus and touch annotations, sensible scroll behavior and a
reduced-motion variant. Keep full first-release functions discoverable on
phones without forcing the tablet layout into a narrow screen. Identify exact
template/module reuse and proposed components; mark server dependencies rather
than fabricating working APIs.

Ask the user to choose between the visual alternatives with the available
multiple-choice chat tool. Settled scope, authority and system decisions are
constraints, not questions to reopen. Record the chosen design and any explicit
revision request in this task and the design document.

## Acceptance

- [ ] All agreed player journeys have annotated states and clear system/role boundaries; no selected feature disappears from the first release.
- [ ] Two comparable Play alternatives are rendered at the three viewports, using identical synthetic examples and no real campaign/private content.
- [ ] Keyboard, touch, compact-phone, reduced-motion, pending-action and reconnect behavior is demonstrated or explicitly annotated.
- [ ] Purchases require GM approval; advancement requires GM unlock; quartermaster powers are confined to loot finalization.
- [ ] Codex reviews scope/contract consistency; the user's visual choice is recorded before any subsequent product UI implementation task can become ready.
- [ ] Mockups work from their documented local entry point without a production connection. Record browser/viewports and actual observations; do not call mockup interactions backend validation.
- [ ] `git diff --check` passes and changed paths are limited to the design/mockup artifacts and this task's report, plus normal queue/log updates.

## Out of scope / do not touch

- Served templates, CSS/JS, backend APIs, migrations, rules, live data or active
  worktrees. These artifacts are designs, not a functioning replacement site.
- New wiki/VTT/map work, Cosmere/Mistborn redesign, or repeating settled product
  scope choices. Keep existing theme foundations and useful accepted mockups.

## Report

Not started. Record mockup entry points, viewport/browser observations, contract
dependencies, user selection and bounded UI implementation slices for Codex.

## Review

Codex records scope/contract findings. Record the user's explicit visual choice
separately; technical review alone does not select the design.
