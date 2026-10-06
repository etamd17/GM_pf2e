# PR5 Private Notes Authorization Remediation Plan

Date: 2026-10-05

Status: approved for implementation; commit and push remain a separate user checkpoint.

**Spec:** `docs/superpowers/specs/2026-10-01-pr5-character-workflows-design.md`, especially section 6: editors may operate character game controls but may not read or mutate owner-private fields.

**Acceptance evidence:** `docs/remediation/pr5-acceptance-2026-10-01.md` and the 2026-10-05 production acceptance finding that an assigned SQL editor could directly export and overwrite private notes.

## Global constraints

- Work only in `remediation/pr5-private-notes-authorization`, based on current `origin/main`.
- Use strict TDD for every behavior change: add a focused regression, observe the expected failure, implement the smallest fix, then rerun the focused and neighboring suites.
- Keep editor gameplay capabilities unchanged. Restrict only owner-private character data.
- Preserve legacy open/password behavior, live-campaign containment, SQL-authoritative assignments, and JSON rejection of forged grants.
- Do not redesign GM or player UI. The only client change replaces an internal raw-export fetch with the existing note-free state endpoint.
- Do not commit, push, merge, deploy, or run production acceptance without a separate explicit user request.

## Review focus

- Direct HTTP calls must not bypass controls hidden by the rendered sheet.
- Owner, campaign GM, and site admin retain private access; editor, viewer, member, and outsider do not.
- Denied writes leave character files byte-for-byte unchanged.
- PF2e export must not expose notes to editors; the condition strip must continue refreshing through `/api/pc_state/<pc_name>`.
- Existing-character reconstruction must preserve owner-private fields even when submitted inputs omit or forge them.
- Editor publication must still apply legitimate non-private changes while private values remain unchanged.
- PF2e and Cosmere, JSON and SQL, direct routes and workflow publication all need coverage.

## Shared interfaces

- Add `RoutePolicy.LIVE_CHARACTER_OWNER_PRIVATE` as a live, character-resolved policy.
- Map only `export_character`, `save_notes`, `save_session_note`, `delete_session_note`, and `cosmere_pc_notes` to that strict policy.
- Keep `RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM` as the existing gameplay edit policy.
- Add a small shared character-workflow privacy helper that copies owner-private build fields from the trusted current document into an updated document and can project those fields out of editor-facing builder data.
- Owner-private build fields are `notes` and `session_notes`; systems that do not currently use one still preserve it defensively when present.

### Task 1: Strict policy and private endpoint boundary

**Interfaces:** route inventory, `core.access.decide_access`, the five private endpoints, and PF2e condition refresh.

1. Add unit and route-inventory regressions for the strict policy and exact endpoint mapping.
2. Run the focused tests and observe failures caused by the missing policy/mapping.
3. Implement the policy decision and route mapping.
4. Change `_refreshConditionStrip` to consume `api_pc_state` rather than raw character export; add or update a behavioral template contract test if one exists.
5. Run the focused access, route-inventory, and sheet tests to green.

### Task 2: Preserve private fields across reconstruction and publication

**Interfaces:** `SystemAdapter.normalize`, `_build_cosmere_document`, workflow builder bootstrap, and publication service.

1. Add adapter/publication regressions proving PF2e and Cosmere private fields survive existing-character updates, omitted fields, and forged replacements.
2. Run them and observe the Cosmere failures.
3. Add the shared preservation/projection helper and apply it to Cosmere reconstruction and editor-facing bootstrap data without weakening publication preservation.
4. Run adapter/publication suites to green.

### Task 3: Backend and browser authorization regressions

**Interfaces:** real SQL/JSON character routes and browser sessions.

1. Replace the SQL test that currently expects editor note writes with a role matrix for owner/GM/editor/viewer/outsider, byte stability, private export, and representative gameplay edits.
2. Add JSON forged-grant route regressions.
3. Add browser acceptance probes for hidden controls, direct 403 responses, and owner sentinel retention.
4. Observe each new regression fail before its corresponding production fix, then run focused SQL, workflow HTTP, and browser tests to green.

### Task 4: Acceptance record and remediation runbook

**Interfaces:** PR5 acceptance evidence only; no product behavior.

1. Update the acceptance record with the failed production gate, exact stop condition, remediation dependency, and requirement to restart all eight gates after deployment.
2. Add `docs/remediation/pr5-private-notes-remediation.md` with root cause, affected surfaces, mitigation, local verification, deployment sequence, and production rerun checklist.

### Task 5: Integrated verification and review

**Interfaces:** all changed code and tests.

1. Run focused authorization, persistence, workflow adapter/publication/HTTP, and browser coverage.
2. Parse all templates and run `git diff --check`.
3. Run the full non-browser suite, the real browser suite, and PostgreSQL-marked tests when the local environment supports them.
4. Dispatch one fresh whole-branch review and resolve Critical/Important findings using RED-to-GREEN tests.
5. Present evidence and remaining production gates to the user. Stop before commit/push.

## Pre-flight shared-interface scan

- Task 1 produces a strict policy consumed by Task 3 route tests: the endpoint inventory must preserve the existing character locator metadata while changing only authorization.
- Task 2 produces preservation/projection helpers consumed by Task 3 SQL/JSON/browser flows: preservation must run on the trusted current document before any editor-facing projection.
- Tasks 1 and 2 jointly satisfy Task 3: endpoint denial prevents direct access, while preservation prevents alternate edit/publication paths from changing private fields.
- Task 4 records Task 5 evidence but does not change runtime contracts.

## Rulings

- The PF2e JSON export is owner-private rather than editor-visible with redacted fields. This matches the existing hidden export control and avoids introducing an incomplete second export format. Cost if wrong: a future editor-facing export would need an explicit, separately tested projection endpoint.
- The existing `/api/pc_state/<pc_name>` response is the condition strip source. It already supplies conditions without private fields. Cost if wrong: any missing condition display field must be added to the state projection rather than reopening raw export access.
- This remediation has no visual-design decision. Existing controls remain visually unchanged, so the user’s mockup-before-UI rule is not triggered.
