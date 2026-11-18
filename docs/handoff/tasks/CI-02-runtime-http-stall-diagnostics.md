# CI-02: Diagnose the intermittent runtime HTTP outage-test timeout

## Brief

- Owner: Claude; Codex reviews.
- Branch: `claude/fix-runtime-outage-test-timeout` from `origin/main` (`3d5e13b8`).
- Problem: on Windows, `tests/persistence/test_runtime_http.py::test_database_outage_never_opens_legacy_gm_access[sql]` sometimes fails with `subprocess.TimeoutExpired` (120 s). The `run_sql` helper boots the whole `app.py` in a child process. It was seen during PE-14b work, 2026-11-17. Linux CI has not hit it.
- Goal: find the root cause and fix it if it is in product code. If it is environmental, make the test robust without hiding a real hang. Measure the boot time.
- Scope: `tests/persistence/test_runtime_http.py` only, unless a product cause is found.
- Acceptance:
  - three full `tests/persistence -m "not postgresql"` runs on Windows with no timeout;
  - a hang, if one happens, reports where the child was stuck;
  - `git diff --check` clean;
  - all four CI jobs green.

## Report

**Root cause: not established.** I could not reproduce the stall on demand. This change is diagnostics, not a fix.

- **Observed failures** (all on this machine, 2026-11-17):
  - two of three full persistence runs in the PE-14b worktree;
  - one run of this test file alone in this worktree on `main`: the `[sql]` case took 120.02 s (timeout), and the `[invalid]` case right after it took 31.3 s against about 14 s normally.
  - Each episode stalled one child for more than 100 s and slowed the next one, which looks like a transient stall outside the process. The episodes coincided with other test activity on the machine (a Codex review session and parallel agents running pytest), but I could not prove that link.
- **Healthy boot:** about 11 to 12 s to import `app.py` (it loads 4,792 monsters), then about 0.1 s for the five requests. Each test case takes about 12 to 15 s.
- **Where nothing was found:**
  - Under pytest the child inherits `PYTEST_CURRENT_TEST`, so `_autostart_persistence` and `_autostart_cosmere_rules` are skipped there and cannot be the wait.
  - No import-time code takes a file lock (searched for `msvcrt`, `fcntl` and lock files).
  - The SQLite database and `DATA_DIR` are per-test temporary paths.
- **Attempts to reproduce, all with no stall:**
  - 16 instrumented boots under pytest (8 per backend): 11.4 to 14.5 s each;
  - the real file three times in a row: 10 passed each time, 130 to 134 s per run, slowest case 14.6 s;
  - three full persistence runs on `main`: **903 passed, 2 skipped, 29 deselected** each time, 360 to 405 s;
  - the real file while a full persistence suite ran alongside: 10 passed, slowest case 13.7 s, and that full suite gave 903 passed, 2 skipped, 29 deselected in 338 s.
- **Change** (`tests/persistence/test_runtime_http.py`, `run_sql` only):
  - The child starts with `faulthandler.dump_traceback_later(CHILD_LIMIT, exit=True)`, with `CHILD_LIMIT = 110`. A child that hangs now dumps every thread's stack to stderr and exits before the parent's limit. The existing `assert result.returncode == 0, stdout + stderr` then shows where it was stuck, instead of a bare `TimeoutExpired` with no output.
  - The parent's limit is `CHILD_LIMIT + 30` (140 s, was 120 s), giving the child's diagnostic exit 30 s of intended headroom. The parent remains a hard fallback if the child cannot arm or run its watchdog. Once armed and able to run, the watchdog fails a hang earlier than before (110 s), so no real hang is hidden.
- **Skipped / not verified:**
  - The stall itself was never caught with the new dump, so the root cause is still open.
  - Linux CI has never shown the stall.
- **Follow-up:** the next time this test fails, read the thread dump in the assertion message. It names the blocking call. Fix that then.

## Review

### Implementation review 1 (Codex, 2026-11-18)

**Accepted at `e4587faa` with no blocking findings.** This is a diagnostic improvement; the intermittent stall remains unexplained.

- The watchdog is armed before schema creation, application imports and the test body. `exit=True` produces a nonzero exit, and the unchanged return-code assertion includes both captured output streams. There is no retry, skip or conversion of a failure into success. The 140 s parent timeout remains a fallback.
- A disposable probe of the actual `run_sql` helper, with only the watchdog interval shortened to one second, injected a blocked main thread and a blocked worker before the application import. Both thread stacks reached the assertion and the child exited with code 1. An injected ordinary exception also failed with its original message. Probe: `../output/ci02-review/timeout_probe.py` (review artifact outside the branch).
- Reviewer rerun: `python -m pytest -q tests/persistence/test_runtime_http.py` -> **10 passed in 122.59 s**. `git diff --check 3d5e13b8..e4587faa` is clean. The report supplies the three full Windows persistence runs required by the brief; the reviewer did not repeat those broad runs for this helper-only change.
- GitHub Actions run **44153449911** on the reviewed head completed successfully in all four jobs: `changes`, `test`, `postgresql` and `browser`. The review-note commit must also pass all four before merge under protocol step 6.
- Non-blocking wording correction applied above: the extra 30 s is intended diagnostic headroom, not a guarantee during process suspension or before the watchdog is armed.

For players: nothing visible changes. This makes an intermittent test failure easier to diagnose while the shared foundations for reliable character actions are being built. PE-14b remains the next pending implementation re-review.
