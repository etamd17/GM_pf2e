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

## Open production checks

The browser-control helper failed before it could discover Chrome tabs:
`windows sandbox failed: helper_unknown_error: apply deny-read ACLs`. This is
not an application test failure. Public health does not prove signed-in flows,
the selected backend, or that the production volume survives restarts.

Using a designated test character in an appropriate test campaign:

1. Sign in as a player, start a draft, enter a name/choices, wait for Saved,
   refresh, and reopen the draft from the account page. Confirm saved input.
2. Finish once, reopen the resulting canonical character sheet, and verify that
   the roster contains exactly one new character. Repeat via a synthetic import.
3. In a second account/session, verify another player's private draft and notes
   are unavailable and assigned viewer/editor access has the intended limits.
4. With an operator and a verified backup, plan a controlled deployment restart;
   confirm the test draft/character survives and the storage boot counter advances.

Do not restart production during an active game solely to complete this checklist.
Staging database cutover and production-volume rollback rehearsal remain separate
operator tasks. Deploying PR4B/PR5 alone does not enable SQL authority.
