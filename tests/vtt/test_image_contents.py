"""The Docker image's .dockerignore is an allowlist. Every repository-root
Python module the app imports (often lazily, inside a function) must be on it,
or the image boots and then fails with ModuleNotFoundError on the first request
that reaches the import. That happened on staging with pf2e_pdf_import.py."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ("core", "services", "systems", "vtt")


def _allowed() -> set[str]:
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    return {line.strip()[1:] for line in lines if line.strip().startswith("!")}


def _sources():
    yield ROOT / "app.py"
    for package in PACKAGES:
        yield from (ROOT / package).rglob("*.py")


def test_every_root_module_the_app_imports_is_in_the_image():
    root_modules = {p.stem for p in ROOT.glob("*.py")}
    pattern = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
    imported = set()
    for source in _sources():
        imported |= set(pattern.findall(source.read_text(encoding="utf-8", errors="replace")))
    needed = sorted(f"{name}.py" for name in imported & root_modules)
    missing = [name for name in needed if name not in _allowed()]
    assert not missing, f"imported but left out of the image by .dockerignore: {missing}"
    assert "pf2e_pdf_import.py" in needed       # the case that broke staging is still covered


def _account_home(**extra):
    import app
    import flask
    campaign = {'id': 'c' * 32, 'name': 'Saga', 'system': 'pf2e', 'members': [{'user_id': 'u1', 'role': 'gm'}]}
    with app.app.test_request_context('/me'):
        return flask.render_template(
            'account_home.html', user={'id': 'u1', 'username': 'gm', 'is_admin': False},
            campaigns=[campaign], gm_campaign_ids=['c' * 32], characters=[], active_campaign_id=None,
            live_campaign_id=None, last_campaign=None, trashed_campaigns=[], trash_ttl_days=30, **extra)


def test_the_campaign_card_links_the_gm_to_the_tabletop_only_where_it_is_on():
    from vtt.transport.pilot import EVERYONE, Pilot
    assert f'href="/vtt/gm?campaignId={"c" * 32}"' in _account_home(vtt_pilot=EVERYONE)
    assert f'href="/vtt/gm?campaignId={"c" * 32}"' in _account_home(vtt_pilot=Pilot(frozenset({"c" * 32})))
    assert '/vtt/gm' not in _account_home(vtt_pilot=None)                       # the VTT is off
    assert '/vtt/gm' not in _account_home(vtt_pilot=Pilot())                    # on, list unset (P-3)
    assert '/vtt/gm' not in _account_home(vtt_pilot=Pilot(frozenset({"d" * 32})))   # another campaign
    assert '/vtt/gm' not in _account_home()            # production's context has no flag
