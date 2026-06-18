"""Guard the tracker's apiPost/apiAction failure attribution.

The production "Request failed" bug: apiPost ran the fetch, the JSON parse, AND
the client-side render (applyState -> render) inside ONE try/catch. So a render
exception on real party data — thrown AFTER the server had already applied the
change and broadcast it over SSE — was caught and toasted as "Request failed".
Every add/remove looked broken even though a refresh showed the change applied.

The fix splits the pipeline into boundaries (network / HTTP status / body parse /
render) so a failure is attributed to the right layer and the toast names it,
and makes the render best-effort so a repaint bug can never masquerade as a
request failure. These are template-wiring + structural guards: the logic is
client JS and the repo has no JS test runner, but the invariants below are the
ones that actually prevent the regression.
"""
from __future__ import annotations

import os
import pathlib

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tracker() -> str:
    return pathlib.Path(_REPO, 'templates', 'tracker.html').read_text()


def _js_fn(src: str, name: str) -> str:
    """Return the source of `async function <name>(...) { ... }` via brace match."""
    marker = 'async function %s(' % name
    i = src.index(marker)
    depth = 0
    started = False
    out = []
    for ch in src[i:]:
        out.append(ch)
        if ch == '{':
            depth += 1
            started = True
        elif ch == '}':
            depth -= 1
            if started and depth == 0:
                break
    return ''.join(out)


def test_apipost_surfaces_failing_layer():
    """The toast must name the layer so a production failure is diagnosable from
    the message alone (no console access needed at the table)."""
    fn = _js_fn(_tracker(), 'apiPost')
    assert 'server returned ' in fn, 'HTTP status not surfaced in the toast'
    assert 'Bad server response' in fn, 'no branch for an OK-but-unparseable body'
    assert 'never reached the server' in fn, 'no branch for a network/fetch reject'


def test_apipost_render_is_best_effort_after_success():
    """applyState (which calls render()) must run AFTER the request is declared a
    success, inside its own guard — so a render throw is never reported as a
    failed request."""
    fn = _js_fn(_tracker(), 'apiPost')
    assert 'pulseSync(true)' in fn and 'applyState(data)' in fn
    # Success is declared (pulseSync(true)) BEFORE the render is attempted.
    assert fn.index('pulseSync(true)') < fn.index('applyState(data)'), \
        'render runs before success is declared — a render throw would look like a failure'
    # The render sits in its own catch that does NOT toast a request failure.
    tail = fn[fn.index('applyState(data)'):]
    assert 'catch' in tail, 'applyState is not guarded by its own catch'
    assert "toast('Request failed'" not in fn, \
        'the old blanket "Request failed" catch is still present'


def test_apiaction_also_surfaces_status():
    """apiAction (turn/init actions) shares the same edge-proxy risk and should
    likewise surface the HTTP status rather than a bare 'Action failed'."""
    fn = _js_fn(_tracker(), 'apiAction')
    assert 'server returned ' in fn
    assert 'never reached the server' in fn


def test_initial_render_runs_after_its_lexical_decls():
    """THE actual root cause of the production "Request failed" — a load-time TDZ.

    The on-load render call (`_renderNow()`) had been hoisted into the bootstrap
    IIFE near the top of the inline script, AHEAD of the top-level `let`s the
    render helpers reference (_lastScrolledActive, _renderScheduled,
    _syncResetTimer, _sessionTimerInterval). At load, `_renderNow()` ->
    `scrollActiveIntoView()` touched `_lastScrolledActive` while it was still in
    its temporal dead zone and threw. That uncaught throw halted the REST of the
    top-level script, so those `let`s never initialized — and from then on
    render() / applyState() / pulseSync() threw a TDZ error on every call. Since
    the page uses inline onclick handlers + server-rendered rows, mutations still
    reached the server (a refresh showed them), but the client repaint always
    threw, surfacing as "Request failed" on every add/remove.

    Invariant: the initial render must come AFTER every render-state `let` it
    depends on, and the bootstrap IIFE must not call _renderNow() early.
    """
    src = _tracker()
    init_call = src.index('try { _renderNow()')  # the trailing INITIAL RENDER call
    for decl in ('let _syncResetTimer', 'let _renderScheduled',
                 'let _lastScrolledActive', 'let _sessionTimerInterval'):
        assert decl in src, 'missing expected declaration %r' % decl
        assert src.index(decl) < init_call, \
            '%s is declared AFTER the initial _renderNow() call — TDZ at load' % decl
    # The bootstrap IIFE must NOT invoke _renderNow() before those lexical
    # declarations have run (that was exactly the regression).
    boot = src.index('Bootstrap initial state from Jinja')
    boot_region = src[boot:src.index('let _syncResetTimer')]
    # Match the call form `_renderNow();` (a statement), not prose mentions of it
    # in the explanatory comment that documents this very regression.
    assert '_renderNow();' not in boot_region, \
        'bootstrap IIFE calls _renderNow() before its lexical deps initialize (TDZ)'
