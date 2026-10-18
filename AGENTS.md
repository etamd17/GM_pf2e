# Agent instructions

Read `CLAUDE.md` first: it is the project guide for every agent working here (Codex
included), not just Claude. Its conventions and high-risk-area rules apply in full.

Work arrives through the shared handoff system:

- `docs/handoff/PROTOCOL.md` -- how tasks are briefed, claimed, reported and reviewed.
- `docs/handoff/QUEUE.md` -- open tasks; take a `ready` one whose owner is you.
- `docs/handoff/tasks/<id>.md` -- the brief; write your report in the same file.

Commit only on the task's branch, never on `main`, and never push to `main`. Once a task is
accepted in review, commit, push, open the PR and merge it (`gh pr merge <n> --merge`) as soon
as every CI job is green; don't wait to be asked (`PROTOCOL.md` step 6).
