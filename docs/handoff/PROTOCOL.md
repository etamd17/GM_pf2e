# Claude <-> Codex handoff protocol

Two agents work this repo. Neither remembers the other's chat, so **everything that
must survive a handoff lives in git**: a brief before work starts, a report when it
ends. The point is to spend tokens on work, not on re-discovering context.

Files:

- `docs/handoff/QUEUE.md` -- the one list of open tasks, who holds each, and status.
- `docs/handoff/tasks/<id>.md` -- one file per task: the brief, then the report.
- Per-area living docs (e.g. `docs/dnd5e/HANDOFF.md`, `docs/map/AUDIT.md`) hold the
  architecture. Briefs link to them instead of repeating them.

## Who takes what

Pick by task shape, not by habit. The aim is that the expensive reasoning happens
once, in the brief, and the implementation is cheap.

| Task shape | Default owner |
|---|---|
| Open design question, or a choice the user must make | Claude writes the options; the **user** decides |
| Writing a brief (files, approach, acceptance tests) | Claude |
| Well-specified implementation from a brief (UI ports from approved mockups, content, small fixes) | Codex |
| Mechanical refactors, renames, test backfill, CI/lint fixes | Codex |
| **Heavy or high-risk coding** (user decision 2026-10-15): rules engines and stat derivation, save formats and migrations, live sync / SSE / persistence / concurrency, auth and route policy, the VTT client, and shared high-traffic files such as `tracker.html` and the map | **Claude implements**; Codex reviews the diff against the brief |
| Anything that deletes or overwrites data | Claude implements, or Claude reviews Codex's diff adversarially |
| UI/UX design: layout, hierarchy, mobile, visual polish | Claude designs and verifies in the browser; Codex may implement the mechanical parts from Claude's brief |
| Browser/UI verification against a running server | Claude (has the browser pane) |
| Review of the other agent's diff | the other agent, **against the brief's acceptance list only** |

Either agent may take any task when the other is unavailable; the table is a default.
When Claude builds, Codex reviews (the same review rules apply in the other direction), and
the Model column in `QUEUE.md` still says how much reasoning the step needs.

## The cycle

1. **Brief** (author: usually Claude). Copy `TEMPLATE.md` to `tasks/<id>.md`, fill the
   brief half, add a row to `QUEUE.md` with status `ready` and the intended owner.
2. **Claim.** The implementer sets the row to `in-progress` and works on the branch
   named in the brief (`<owner>/<id>-<slug>`, cut from the branch in the brief).
3. **Implement.** Stay inside the brief's scope. Anything found out of scope goes in the
   report's *Follow-ups*, not into the diff.
4. **Report.** Fill the report half of the task file: what changed, the exact test
   commands run with their real numbers, what was skipped, what is still wrong. Set the
   row to `review`, and add one plain line to the **Log** at the bottom of `QUEUE.md`
   (who, what was done, what's next). The user reads the log between handoffs: plain
   words, no code terms.
   Also end your chat reply with the same thing as a few short bullets: done / next /
   which tool and model to use for the next step.
5. **Review** (the other agent). Check the diff against the acceptance list and the
   high-risk rules in `CLAUDE.md`. Either `done`, or back to `in-progress` with numbered
   findings appended to the task file.
6. **Commit, PR and merge** (standing rule from the user, 2026-10-15). Once the reviewer
   accepts a task, the implementer commits (task file and queue edits included), pushes
   and opens the PR without waiting to be asked. Merge it with `gh pr merge <n> --merge` as
   soon as **every** CI job on the PR has finished green. Don't merge while a job is still
   running or failing, and don't use auto-merge (this repo has no required checks, so it
   merges at once). Docs-only PRs skip the test jobs and can merge when `changes` passes.
   A red job goes back to the implementer. Never push directly to `main`; merges go through
   a PR. `main` deploys to the live site, so this rule is what ships.
   Keep few branches open: one per task, merged promptly, so `QUEUE.md` doesn't conflict.

## Rules that save usage

- **A brief must be executable cold.** Exact file paths and functions, the approach in a
  few sentences, the acceptance tests as commands, and what NOT to touch. If the
  implementer has to explore to understand the task, the brief was too thin. That is
  cheaper to fix in the brief than in two agents' context windows.
- **Link, don't paste.** Point at `file.py:123` and the living doc section; never paste
  large code or doc excerpts into a brief.
- **Small tasks.** One brief = one reviewable diff (roughly under 500 changed lines).
  Split bigger work into several numbered tasks with dependencies noted in `QUEUE.md`.
- **Report numbers, not prose.** `pytest -q tests/test_x.py -> 14 passed` beats a
  paragraph. A reviewer re-runs only what looks wrong.
- **Review against the acceptance list.** Not a fresh audit of the whole area, unless
  the task touched a high-risk surface.
- **Never redo the other agent's exploration.** If the brief or the living doc is wrong,
  fix the doc as part of the task so the next handoff starts right.

## Repo conventions both agents follow

`CLAUDE.md` is authoritative for both agents (Codex reads it via `AGENTS.md`):
no emojis, commit only on a branch (see step 6 for when), `encoding='utf-8'`
in tests, `python tools/check_templates.py` after any template edit, the
`[hidden]`-vs-`display` rule, inline-handler escaping, every new endpoint in
`core/route_policy.py`.
