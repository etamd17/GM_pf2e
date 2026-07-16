"""Chronicle PR1 — publish endpoint, leak/manifest validation, markdown render,
status + rollback. Subprocess isolation (fresh DATA_DIR, legacy-open GM mode),
mirroring tests/test_campaign_backup.py."""
from __future__ import annotations
import io, os, sys, json, zipfile, subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    return subprocess.run(
        [sys.executable, '-c', "import os, sys\nsys.path.insert(0, os.getcwd())\n" + body],
        capture_output=True, text=True, cwd=_REPO)


def test_chronicle_prefix_is_gm_gated():
    # The prefix must be present in the centralized GM gate so every
    # /api/chronicle/* route is GM-only with no per-route decorator.
    import app as A  # imported in-process here is fine: pure constant check
    assert '/api/chronicle' in A.GM_API_PREFIXES


def test_leak_scan_flags_forbidden_markers(tmp_path):
    import app as A
    (tmp_path / 'content').mkdir()
    (tmp_path / 'content' / 'clean.md').write_text('# Recap\nThe party arrived.\n')
    (tmp_path / 'content' / 'leaky.md').write_text('> [!danger] the lich is the mayor\n')
    (tmp_path / 'manifest.json').write_text('{"note": "has a [!secret] in json too"}')
    offenders = A._chronicle_leak_scan(str(tmp_path))
    assert any('leaky.md' in o and '[!danger]' in o for o in offenders), offenders
    assert any('manifest.json' in o and '[!secret]' in o for o in offenders), offenders
    assert not any('clean.md' in o for o in offenders), offenders
    # clean tree -> empty list
    (tmp_path / 'content' / 'leaky.md').unlink()
    (tmp_path / 'manifest.json').write_text('{"note": "ok"}')
    assert A._chronicle_leak_scan(str(tmp_path)) == []


def test_render_markdown_callouts_and_sanitize():
    import app as A
    md = (
        "# Session 3\n\n"
        "The party met **Romi**.\n\n"
        "> [!quote] Romi\n> We never had this conversation.\n\n"
        "> [!example] Handout\n> A torn ledger page.\n\n"
        "> [!note] table cue\n> keep this plain\n\n"
        "<script>alert(1)</script>\n\n"
        "[click](javascript:alert(2))\n"
    )
    html = A._chronicle_render_markdown(md)
    assert '<h1' in html and '<strong>Romi</strong>' in html
    assert 'class="callout-quote"' in html
    assert 'class="doc-frame"' in html
    assert '<blockquote>' in html          # unknown callout -> plain blockquote
    assert '[!quote]' not in html and '[!note]' not in html   # markers consumed
    assert '<script' not in html.lower()   # sanitized
    assert 'javascript:' not in html.lower()


def test_safe_slug():
    import app as A
    assert A._chronicle_safe_slug("Romi's Ledger") == 'romi-s-ledger'
    assert A._chronicle_safe_slug('../etc/passwd') == 'etc-passwd'
    assert A._chronicle_safe_slug('') == 'page'
