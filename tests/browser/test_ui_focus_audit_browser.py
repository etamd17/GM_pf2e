"""The focus audit follows real Tab order and measures what Chromium paints."""
import importlib
import contextlib
from concurrent.futures import ThreadPoolExecutor
import functools
import http.server
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading

import pytest

from tools import ui_screens

pytestmark = pytest.mark.browser


def _audit(page, tmp_path, **kwargs):
    assert callable(getattr(ui_screens, "focus", None)), "the focus command is missing"
    tool = importlib.import_module("tools.ui_focus")
    return tool.audit_page(page, tmp_path, "scratch", **kwargs)


def _page(browser, body):
    context = browser.new_context(viewport={"width": 600, "height": 400}, reduced_motion="reduce")
    page = context.new_page()
    page.set_content("""<style>
        body { background:#111; margin:24px; }
        button { margin:10px; padding:8px; background:#222; color:white; }
        button:focus-visible { outline:2px solid #e0b65a; outline-offset:2px; }
        </style>""" + body)
    return context, page


def test_real_tab_walk_keeps_order_and_saves_focused_evidence(browser, tmp_path):
    context, page = _page(browser, '<button id=first>First</button><button id=second>Second</button>')
    try:
        before = page.locator("button").evaluate_all("es => es.map(e => e.outerHTML)")
        rows, failures = _audit(page, tmp_path)
        assert not failures, failures
        assert [r["id"] for r in rows] == ["first", "second"]
        assert page.locator("button").evaluate_all("es => es.map(e => e.outerHTML)") == before
        assert list(tmp_path.glob("*.png"))
    finally:
        context.close()


@pytest.mark.parametrize("style", ["outline-color:transparent", "opacity:0", "outline-color:#222"])
def test_a_solid_but_invisible_ring_fails_the_actual_audit(browser, tmp_path, style):
    context, page = _page(browser, f'<button id=bad style="{style}">Bad</button>')
    try:
        rows, failures = _audit(page, tmp_path)
        assert len(rows) == 1 and failures
    finally:
        context.close()


def test_effective_ancestor_opacity_is_measured_without_automatically_rejecting_dim_rows(browser, tmp_path):
    context, page = _page(browser, '<div style="opacity:.55"><button id=dim>Dim</button></div>')
    try:
        rows, failures = _audit(page, tmp_path)
        assert not failures, failures
        assert rows[0]["effectiveOpacity"] == pytest.approx(.55)
        assert rows[0]["contrast"] >= 3
    finally:
        context.close()


@pytest.mark.parametrize('opacity', [1, .55])
def test_ring_matching_half_the_surface_is_not_a_clean_contrast_pass(browser, tmp_path, opacity):
    context, page = _page(browser, f'''
        <style>html{{background:linear-gradient(90deg,#e0b65a 50%,#111 50%)}}
        body{{margin:0;background:transparent}}#mixed{{position:absolute;left:250px;top:100px;
        width:100px;height:40px;margin:0;padding:0;border:0}}</style>
        <div style="opacity:{opacity}"><button id=mixed>Mixed surface</button></div>''')
    try:
        rows, failures = _audit(page, tmp_path)
        assert failures, 'the half of the ring on identical gilt has 1:1 contrast'
        assert rows[0]['contrast'] == 1
        assert rows[0]['sameColorSamples'] > 0
    finally:
        context.close()


@pytest.mark.parametrize('surface', ['#dfb65a', '#e0b55a'])
def test_ring_nearly_matching_half_the_surface_is_not_a_clean_contrast_pass(browser, tmp_path, surface):
    context, page = _page(browser, f'''
        <style>html{{background:linear-gradient(90deg,{surface} 50%,#111 50%)}}
        body{{margin:0;background:transparent}}#mixed{{position:absolute;left:250px;top:100px;
        width:100px;height:40px;margin:0;padding:0;border:0}}</style>
        <button id=mixed>Nearly matching surface</button>''')
    try:
        rows, failures = _audit(page, tmp_path)
        assert failures, 'a one-step RGB difference is invisible, not proof of clipping'
        assert rows[0]['missingSamples'] > 0
        assert rows[0]['sameColorSamples'] == 0
        assert rows[0]['contrast'] is None or rows[0]['contrast'] < 3
    finally:
        context.close()


@pytest.mark.parametrize('opacity', [1, .55])
def test_unchanged_clipped_edges_remain_distinct_from_same_colour_paint(browser, tmp_path, opacity):
    context, page = _page(browser, f'''<div style="overflow:hidden;width:100px;padding-top:5px;opacity:{opacity}">
        <button id=partial style="display:block;width:100px;height:30px;margin:0;padding:0">Clipped edges</button></div>''')
    try:
        rows, failures = _audit(page, tmp_path)
        assert rows[0]['missingSamples'] > 0
        # The screenshot alone cannot prove why these in-image samples did not
        # change. Neither opaque nor translucent missing paint is a clean pass.
        assert failures
        assert 'unchanged samples' in rows[0]['measurementError']
        assert rows[0]['contrast'] is None
    finally:
        context.close()


@pytest.mark.parametrize('opacity', [1, .55])
def test_an_inset_ring_inside_a_clipped_container_is_fully_measured(browser, tmp_path, opacity):
    context, page = _page(browser, f'''<div style="overflow:hidden;width:100px;opacity:{opacity}">
        <button id=inset style="display:block;width:100px;height:30px;margin:0;padding:0;
            border:0;outline-offset:-2px">Inset edges</button></div>''')
    try:
        rows, failures = _audit(page, tmp_path)
        assert not failures, failures
        assert rows[0]['outlineOffset'] == -2
        assert rows[0]['missingSamples'] == rows[0]['uncertainSamples'] == 0
        assert rows[0]['contrast'] >= 3
    finally:
        context.close()


def test_a_truncated_tab_walk_is_not_a_pass(browser, tmp_path):
    context, page = _page(browser, '<button>One</button><button>Two</button><button>Three</button>')
    try:
        _, failures = _audit(page, tmp_path, max_stops=2)
        assert any("limit" in failure for failure in failures)
    finally:
        context.close()


def test_tab_scrolling_a_control_below_the_fold_keeps_its_ring_coordinates(browser, tmp_path):
    context, page = _page(browser, '<button id=below style="margin-top:1200px">Below fold</button>')
    try:
        rows, failures = _audit(page, tmp_path)
        assert page.evaluate('scrollY') > 0
        assert not failures, failures
        assert rows[0]['contrast'] >= 3
    finally:
        context.close()


def test_empty_stretched_link_uses_its_real_absolute_after_focus_indicator(browser, tmp_path):
    context, page = _page(browser, '''
        <style>
        .tile{position:relative;width:200px;height:100px;padding:10px;border:4px solid #333}
        .tile a::after{content:"";position:absolute;inset:0}
        .tile a:focus-visible::after{outline:2px solid #e0b65a;outline-offset:-2px}
        </style><div class=tile><a id=stretched href=#x aria-label="Open tile"></a></div>''')
    try:
        expected = page.locator('.tile').evaluate('''el => {
            const r = el.getBoundingClientRect();
            return {x:r.x + el.clientLeft,y:r.y + el.clientTop,
                    width:el.clientWidth,height:el.clientHeight};
        }''')
        before = page.locator('#stretched').evaluate('el => el.outerHTML')
        rows, failures = _audit(page, tmp_path)
        assert not failures, failures
        assert rows[0]['indicator'] == '::after'
        assert rows[0]['controlRect']['width'] == 0
        assert rows[0]['rect'] == pytest.approx(expected)
        assert rows[0]['contrast'] >= 3
        assert page.locator('#stretched').evaluate('el => el.outerHTML') == before
    finally:
        context.close()


def test_zero_geometry_without_a_painted_pseudo_indicator_still_fails(browser, tmp_path):
    context, page = _page(browser, '<a id=empty href=#x aria-label="Empty"></a>')
    try:
        rows, failures = _audit(page, tmp_path)
        assert len(rows) == 1 and failures
        assert rows[0]['contrast'] is None
    finally:
        context.close()


def test_finite_modal_transition_settles_before_geometry_and_opacity_are_recorded(browser, tmp_path):
    context, page = _page(browser, '''
        <style>#modal{transform:scale(.2);opacity:0;transition:transform .25s,opacity .25s;transform-origin:top left}
        #modal.open{transform:scale(1);opacity:1}</style>
        <div id=modal><button id=inside style="width:100px;height:40px">Inside</button></div>
        <script>document.querySelector('#inside').addEventListener('focus',()=>document.querySelector('#modal').classList.add('open'));</script>''')
    try:
        rows, failures = _audit(page, tmp_path)
        assert not failures, failures
        assert rows[0]['effectiveOpacity'] == 1
        assert rows[0]['rect']['width'] == pytest.approx(100)
    finally:
        context.close()


def test_roving_discovery_requires_visible_explicit_one_zero_and_other_minus_one_tabs(browser):
    tool = importlib.import_module('tools.ui_focus')
    context, page = _page(browser, '''
        <div role=tablist id=ordinary><button>One</button><button>Two</button></div>
        <div role=tablist id=hidden hidden><button role=tab tabindex=0>Hidden one</button><button role=tab tabindex=-1>Hidden two</button></div>
        <div role=tablist id=real><button role=tab tabindex=0>Real one</button><button role=tab tabindex=-1>Real two</button></div>''')
    try:
        assert callable(getattr(tool, 'discover_roving', None)), 'roving discovery is missing'
        widgets, diagnostics = tool.discover_roving(page)
        assert not diagnostics
        assert len(widgets) == 1 and widgets[0]['id'] == 'real'
        assert widgets[0]['count'] == 2
    finally:
        context.close()


def test_real_party_tab_markup_stays_ordinary_native_tab_order(browser, tmp_path):
    # Use the real template's full pc-tabs block, keeping every possible button.
    # Removing only Jinja branching/count expressions needs no app import/data.
    source = (Path(__file__).resolve().parents[2] / 'templates/party_view.html').read_text(encoding='utf-8')
    block = re.search(r'<div class="pc-tabs" role="tablist">.*?</div>', source, re.S).group()
    block = re.sub(r'{%.*?%}|{{.*?}}', '', block, flags=re.S)
    context, page = _page(browser, block)
    try:
        tool = importlib.import_module('tools.ui_focus')
        widgets, diagnostics = tool.discover_roving(page)
        assert not widgets and not diagnostics
        expected = page.locator('.pc-tab').evaluate_all("es => es.map(el => el.textContent.trim())")
        assert len(expected) > 2
        assert page.locator('.pc-tab').evaluate_all("es => es.every(el => el.tabIndex === 0 && el.getAttribute('tabindex') === null)")
        rows, failures = _audit(page, tmp_path)
        assert not failures, failures
        assert [row['label'] for row in rows] == expected
    finally:
        context.close()


@pytest.mark.parametrize('control', [
    '<button style="border-radius:50%;width:80px;height:80px">Round</button>',
    '<button style="border-radius:50%;width:44px;height:44px;padding:0">Small round</button>',
    '<button style="border-radius:50%;width:42px;height:42px;padding:0">Smaller round</button>',
    '<input type=checkbox style="margin:30px;outline:2px solid #e0b65a;outline-offset:2px">',
])
def test_circle_and_checkbox_outlines_have_visible_core_samples(browser, tmp_path, control):
    context, page = _page(browser, control)
    try:
        rows, failures = _audit(page, tmp_path)
        assert not failures, failures
        assert rows[0]['contrast'] >= 3
    finally:
        context.close()


@pytest.fixture
def served(tmp_path, monkeypatch):
    base = '<style>body{background:#111;margin:30px}button{margin:10px;padding:8px}button:focus-visible{outline:2px solid #e0b65a;outline-offset:2px}</style>'
    (tmp_path / 'good.html').write_text(base + '<button>Good</button>', encoding='utf-8')
    (tmp_path / 'bad.html').write_text(base + '<button style="outline:2px solid transparent">Bad</button>', encoding='utf-8')
    (tmp_path / 'empty.html').write_text(base + '<a id=empty href=#x aria-label="Empty" '
        'style="display:block;width:0;height:0;border-radius:0;padding:0;border:0"></a>', encoding='utf-8')
    (tmp_path / 'tabs.html').write_text(base + '''
        <button id=hidden-tab class=tab-btn hidden>Hidden</button>
        <button id=visible-tab class=tab-btn onclick="document.querySelector('#tab-panel-control').hidden=false">Visible</button>
        <button id=tab-panel-control hidden>Opened panel</button>''', encoding='utf-8')
    roving = '''<div role=tablist><button id=tab-one role=tab tabindex=0>One</button><button id=tab-two role=tab tabindex=-1>Two</button></div>
        <button id=first-panel>First panel</button><button id=second-panel hidden>Second panel</button>'''
    (tmp_path / 'inert-roving.html').write_text(base + roving, encoding='utf-8')
    (tmp_path / 'roving.html').write_text(base + roving + '''<script>
        const tabs=Array.from(document.querySelectorAll('[role=tab]'));
        tabs.forEach(tab=>tab.addEventListener('keydown',event=>{
            if(event.key!=='ArrowRight') return;
            event.preventDefault();
            const next=tabs[(tabs.indexOf(tab)+1)%tabs.length];
            tabs.forEach(el=>el.tabIndex=el===next?0:-1);
            document.querySelector('#first-panel').hidden=next.id!=='tab-one';
            document.querySelector('#second-panel').hidden=next.id!=='tab-two';
            next.focus();
        }));
        </script>''', encoding='utf-8')
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0),
        functools.partial(Quiet, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    @contextlib.contextmanager
    def fixture_server(repo, profile, routes=None):
        yield server.server_address[1], None, [('gm', route, route) for route in routes]
    monkeypatch.setattr(ui_screens, '_served', fixture_server)
    monkeypatch.setattr(ui_screens, 'VIEWPORTS', {'test': (600, 400)})
    original_pages = ui_screens._pages
    def short_timeout_pages(*args, **kwargs):
        for entry in original_pages(*args, **kwargs):
            if not isinstance(entry[-1], Exception):
                entry[-1].set_default_timeout(500)
            yield entry
    monkeypatch.setattr(ui_screens, '_pages', short_timeout_pages)
    try:
        yield
    finally:
        server.shutdown()


@pytest.mark.parametrize('route, expected', [('/good.html', 0), ('/bad.html', 1)])
def test_focus_command_runs_real_http_and_fails_a_suppressed_control(served, tmp_path, route, expected):
    out = tmp_path / 'evidence'
    # Start the CLI's own Playwright instance outside the browser fixture loop.
    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(ui_screens.main, ['focus', '--routes', route, '--out', str(out)]).result()
    assert result == expected
    report = json.loads((out / 'focus.json').read_text(encoding='utf-8'))
    method = report['measurement_method']
    assert '12 outline sample positions' in method
    assert 'best pixel in a 3x3 window' in method
    assert 'not a whole-perimeter minimum' in method
    assert 'not a WCAG certificate' in method
    assert bool(report['problems']) == bool(expected)
    assert len(report['pages']) == 1
    assert report['pages'][0]['stops'][0]['screenshot']


def test_real_zero_size_control_produces_a_strictly_parseable_json_report(served, browser, tmp_path):
    out = tmp_path / 'empty-evidence'
    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(ui_screens.main, ['focus', '--routes', '/empty.html', '--out', str(out)]).result()
    assert result == 1, 'a control with no painted geometry must still fail the gate'
    source = (out / 'focus.json').read_text(encoding='utf-8')
    context = browser.new_context()
    try:
        report = context.new_page().evaluate('source => JSON.parse(source)', source)
        row = report['pages'][0]['stops'][0]
        assert row['rect']['width'] == row['rect']['height'] == 0
        assert row['radii'] == [[0, 0]] * 4
        assert row['problems']
    finally:
        context.close()


def test_outline_inside_a_clipped_container_is_not_mistaken_for_a_visible_ring(browser, tmp_path):
    context, page = _page(browser, '<div style="overflow:hidden;width:100px"><button id=clipped style="display:block;width:100px;margin:0">Clipped</button></div>')
    try:
        rows, failures = _audit(page, tmp_path)
        assert len(rows) == 1 and failures
        assert rows[0]['missingSamples'] == 12
        assert 'hiddenOutline' in rows[0]
        assert rows[0]['hiddenOutline']['outlineAlpha'] == 0
        assert (tmp_path / rows[0]['backgroundScreenshot']).exists()
    finally:
        context.close()


@pytest.mark.parametrize('route, expected', [('/roving.html', 0), ('/inert-roving.html', 1)])
def test_extra_roving_state_requires_real_arrow_movement(served, tmp_path, route, expected):
    out = tmp_path / 'roving-evidence'
    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(ui_screens.main, ['focus', '--routes', route, '--out', str(out)]).result()
    report = json.loads((out / 'focus.json').read_text(encoding='utf-8'))
    assert result == expected, report['problems']
    if expected:
        assert any('ArrowRight did not move focus' in problem for problem in report['problems'])
    else:
        assert any(row['id'] == 'second-panel' and '_roving-' in row['state']
                   for page in report['pages'] for row in page['stops'])


def test_hidden_sheet_style_tab_is_not_a_required_click_state(served, tmp_path):
    out = tmp_path / 'tab-evidence'
    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(ui_screens.main, ['focus', '--routes', '/tabs.html', '--out', str(out)]).result()
    report = json.loads((out / 'focus.json').read_text(encoding='utf-8'))
    assert result == 0, report['problems']
    states = {row['state'] for page in report['pages'] for row in page['stops']}
    assert any(state.endswith('_visible-tab') for state in states)
    assert not any(state.endswith('_hidden-tab') for state in states)
    assert any(row['id'] == 'tab-panel-control' for page in report['pages'] for row in page['stops'])


def test_cli_file_entrypoint_works_outside_the_repo_with_a_disposable_fixture(tmp_path):
    # Exercise Python's script import path, not a pytest import or a mocked CLI.
    # The fixture module avoids importing the real app/compendium in this test.
    (tmp_path / 'tools').mkdir()
    (tmp_path / 'tools' / 'ui_inventory.py').write_text('''
from contextlib import contextmanager
from types import SimpleNamespace
from flask import Flask
FIXTURE_PCS = ()
DEFAULT_ROUTES = ('/',)
@contextmanager
def _isolated_app(data_dir):
    app = Flask(__name__)
    @app.route('/')
    def index():
        return '<style>body{background:#111;margin:30px}button:focus-visible{outline:2px solid #e0b65a;outline-offset:2px}</style><button>Fixture</button>'
    yield SimpleNamespace(app=app)
''', encoding='utf-8')
    result = subprocess.run([sys.executable, str(Path(ui_screens.__file__).resolve()),
        'focus', '--repo', str(tmp_path), '--out', str(tmp_path / 'evidence')],
        cwd=tmp_path, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
        capture_output=True, text=True, encoding='utf-8', timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / 'evidence' / 'focus.json').read_text(encoding='utf-8'))
    assert len(report['pages']) == 2 and not report['problems']
