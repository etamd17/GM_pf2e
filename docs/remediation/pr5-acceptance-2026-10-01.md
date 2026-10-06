# PR5 release acceptance: 2026-10-01

Revision: `e669facd5012dbab5603761121779217654cd0d7`, GitHub PR #162.

## Verified

- Railway records deployment success for this revision, deployment
  `24bf34e6-6b1a-47ab-9c7b-fc69161d87ed`.
- Public `/live`, `/ready`, `/health`, login and both new workflow JS assets
  return HTTP 200. Anonymous account/character access requires login.
- Premerge CI: 3,197 passed, 38 skipped, 45 deselected, one existing expected
  failure; browser 32 passed; PostgreSQL 13 passed.
- Main run `36939168189` completed successfully: 3,197 regression tests passed,
  38 skipped, 45 deselected, one existing expected failure; browser 32 passed;
  PostgreSQL 13 passed. Production-only Gunicorn/gevent smoke and all 92 template
  parses also passed. Counts were checked against the completed job logs.
- Browser cases use isolated synthetic accounts and campaigns and cover both
  systems, both backends, desktop/mobile, drafts, imports, recovery and privacy.
- Local PR6A verification, not part of deployed `e669facd`: reproduced the
  handoff's Windows-only test issue. The PF2e adapter fixture
  crashed while decoding `player_builder.html` with cp1252. Its file read now
  explicitly uses UTF-8. This changes test portability, not application behavior.

## Production disposition update: 2026-10-05

**Status: BLOCKED — PR5 is not accepted.**

The production/deployment baseline and the focused, browser, template,
PostgreSQL, and full regression suites were healthy. During an out-of-order SQL
role-boundary preflight, however, an assigned editor directly exported and
overwrote an owner's private notes even though the rendered character sheet hid
the owner-private controls. This is a server-side authorization and privacy
defect, not a browser-control or presentation failure.

Acceptance stopped immediately. Gate 6 failed; gates 2 through 5 and gate 7 were
not completed, and gate 8 was not reached. Passing automated suites do not close
production acceptance. PR5 remains incomplete until a separate tested
remediation is deployed and all eight gates below are rerun from gate 1.

## Eight-gate production sequence

Use named synthetic accounts, a named test draft, and named test characters in
an appropriate test campaign. Record identifiers without copying private note
contents into logs or screenshots.

| Gate | Required evidence | 2026-10-05 result |
|---:|---|---|
| 1. Production/deploy baseline | Confirm the expected `main` revision and Railway deployment, authenticated GM `/health`, healthy storage flags, `persistence_proven=true`, and record `boots_observed` before mutation. | Passed before the stop condition. |
| 2. Normal-player draft/resume | Save a player draft, observe Saved, refresh, return to `/me`, reopen it, and verify the name plus at least one non-name choice. | Not completed. |
| 3. Builder publication | Finish once, land on canonical `/characters/<id>`, and verify exactly one matching `Your Characters` entry. | Not completed. |
| 4. Synthetic import | Preview/import as a new private owned character, publish once, receive a second canonical ID, and verify exactly one matching account entry. | Not completed. |
| 5. Cross-account privacy | From player B, prove player A's unpublished draft is absent/denied, the private-note marker is absent, and non-owner raw JSON retrieval is denied. | Not completed. |
| 6. Viewer/editor authorization | Respect the active backend. JSON is a justified N/A for SQL assignments; never switch production authority merely for acceptance. In existing SQL mode, editor gameplay controls remain available but owner-private export/notes are denied; PF2e viewer is read-only and note-free; Cosmere viewer remains denied. | **Failed.** An assigned SQL editor could directly export and overwrite owner-private notes. |
| 7. Backup and controlled restart | Verify a full `DATA_DIR` backup and matching SQL snapshot when enabled, perform a non-game restart, confirm health and an incremented boot counter, then verify the draft, both characters, and private marker each persist exactly once. | Not completed. |
| 8. Cleanup and record | Discard only the named test draft; remove named test characters only when authorized and safe; record anything intentionally left behind. | Not reached. |

The remediation contract, local verification, deploy order, and rerun worksheet
are in [the private-notes remediation runbook](pr5-private-notes-remediation.md).
Do not restart production during an active game solely to complete this sequence.
Staging database cutover and production-volume rollback rehearsal remain separate
operator tasks. Deploying PR4B/PR5 alone does not enable SQL authority.
