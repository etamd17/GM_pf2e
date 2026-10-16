# CI-01: Run PF2e rules tests once

## Brief

- Owner: Codex; Claude reviews.
- Branch: `codex/ci-pf2e-once` from `origin/main` (`20b47773`).
- User request: preserve CI coverage and stop running PF2e tests twice.
- Change `.github/workflows/ci.yml`: keep the dedicated `tests/pf2e_rules` step unchanged;
  add `--ignore=tests/pf2e_rules` to the subsequent full-suite invocation.
- Acceptance: both steps together select the same unique tests as before, with no overlap;
  YAML parses and diff check passes.
- Scope: no test, product code, dependency, job gate or timeout changes.

## Report

- Changed only the later pytest command: added `--ignore=tests/pf2e_rules` and a comment
  explaining that its dedicated step already ran. That earlier step and its failure gate
  are unchanged. Browser and PostgreSQL jobs are unchanged.
- `pytest --collect-only -q -p no:cacheprovider tests/pf2e_rules --strict-markers`:
  **950 tests collected in 1.45 seconds**. These tests still run in the dedicated step.
- YAML parsing and exact-command checks pass. Tracked test-file partition:
  **304 = 17 PF2e + 287 remaining**, no omitted files or overlap. The later step retains
  its original marker expression; only the already-covered directory is excluded.
- `git diff --check` passes.
- A full selected-test-ID comparison was attempted with collection only, but was interrupted
  when whole-repository collection was slow. No complete before/after node-ID count is claimed.
  No full test suite was run locally for this workflow-only change.
- Expected saving: roughly **12 minutes per full CI run**, based on the recent dedicated
  PF2e step duration. Existing PR #209's running workflow is unaffected.
- Claude accepted the focused review below. Publishing from updated main; merge remains gated
  on every CI job succeeding.

## Review

### Claude, 2026-10-16: ACCEPTED

No required fixes. Coverage is preserved:

- **Superset.** The dedicated step (`pytest -q tests/pf2e_rules --strict-markers`) has no
  marker filter. With the full suite's filter (`-m "not postgresql and not browser"`) the
  directory also collects **950**, so the dedicated step selects everything the full suite used
  to run there. No test in `tests/pf2e_rules` carries either marker.
- **Order.** The dedicated step runs before the D&D 5e data build; the full suite runs after
  it. This was the one way coverage could have dropped. No PF2e rules test reads `dnd5e_data`.
  Its 14 skips are all host-capability checks (symlink and hard-link support), which behave
  the same in both steps on the same runner.
- **Same environment.** Both steps are in the same `test` job with the same env and
  `--strict-markers`.
- **No hidden coupling.** No other test imports from `tests/pf2e_rules`, and there is no
  `collect_ignore` or `addopts` in `pytest.ini` or `conftest.py` that would interact with
  `--ignore`.
- **YAML.** It parses, and the two pytest commands are exactly as described.
  `git diff --check` is clean.

Notes, not blocking:

- The branch is based on `20b47773`. PR #209 (MAP-R2/R3) also edits `docs/handoff/QUEUE.md`,
  so rebase onto `main` after #209 merges and keep both log lines.
- The new log line is followed by a blank line, which turns that list into a loose list. Drop
  the blank line when rebasing.
- After merge, the first full CI run should show the `Run tests` step about 12 minutes
  shorter, with the PF2e count unchanged in the dedicated step. Worth a glance; it is the only
  end-to-end confirmation of the saving.
