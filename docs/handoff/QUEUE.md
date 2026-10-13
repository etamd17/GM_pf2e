# Task queue

Protocol: `docs/handoff/PROTOCOL.md`. Status: `blocked` / `ready` / `in-progress` /
`review` / `done`. Owner is the default; either agent may pick up a `ready` task.
A task without a brief file is not `ready`.

| ID | Task | Owner | Status | Depends | Brief |
|---|---|---|---|---|---|
| 5E-00 | Decide how the 5etools data ships (repo is public) -- decided: keep it out of git; volume install + CI build | user | done | - | `docs/dnd5e/HANDOFF.md` > Rules data |
| 5E-01 | Commit `feature/dnd5e` (verify no `dnd5e_data/` content is staged), open a PR, get CI green (browser + PostgreSQL jobs and the new 5e data build have never run in CI) | claude | ready (awaiting user go-ahead to commit) | 5E-00 | - |
| 5E-02 | After merge: install the data bundle on Railway at `/dnd5e/admin/data` (`tools/dnd5e/bundle.py`), confirm it survives a redeploy, then check `/dnd5e/*` pages, a PDF import and a portrait upload live | claude + user | blocked | 5E-01 | - |
| 5E-03 | SQL ownership adapter for 5e characters (needed before the web process gets `DATABASE_URL`) | claude | blocked | 5E-01 | - |
| 5E-04 | Species versions as a required builder choice (Draconic Ancestry, 2024 lineages/legacies) + fix raw `{'choose'}` text in sheet resistances | codex | ready | - | `tasks/5E-04.md` |
| 5E-05 | Author-private drafts for 5e characters via `core/character_workflows` | claude | needs brief | 5E-03 | - |
| 5E-06 | Delete combatant pictures nothing uses | codex | ready | - | `tasks/5E-06.md` |
