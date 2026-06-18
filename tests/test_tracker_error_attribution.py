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
