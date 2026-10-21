"""L-1: while the VTT is on for the active campaign, the GM navigation shows
Tabletop where it showed the Map; /map stays reachable by its address (the
launch fallback). With the VTT off, or the campaign outside the pilot list,
the navigation is exactly as before."""

from __future__ import annotations

from types import SimpleNamespace

import flask
import pytest

import app
from vtt.transport.pilot import EVERYONE, Pilot

CID = "c" * 32
TABLETOP = f'href="/vtt/gm?campaignId={CID}"'


@pytest.fixture
def hub(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "_active_campaign_id", lambda: CID)

    def render(pilot, *, gm=True):
        monkeypatch.setattr(app, "VTT_SERVICE", None if pilot is None else SimpleNamespace(pilot=pilot))
        monkeypatch.setattr(app, "_is_gm", lambda: gm)
        with app.app.test_request_context("/gm"):
            return flask.render_template_string(
                '{% extends "base.html" %}{% block content %}{% include "_l1_probe.html" %}{% endblock %}')
    return render


@pytest.fixture(autouse=True)
def probe_template(tmp_path, monkeypatch):
    # The GM hub's tile markup, rendered on its own: the hub route needs a live campaign.
    import jinja2
    source = open("templates/gm_hub.html", encoding="utf-8").read()
    start = source.index("{# L-1:")
    tile = source[start:source.index("</a>", start) + 4]
    env = app.app.jinja_env
    monkeypatch.setattr(env, "loader", jinja2.ChoiceLoader([jinja2.DictLoader({"_l1_probe.html": tile}), env.loader]))


def test_vtt_on_for_this_campaign_shows_tabletop_and_no_map_link(hub):
    for pilot in (EVERYONE, Pilot(frozenset({CID}))):
        page = hub(pilot)
        assert page.count(TABLETOP) == 2           # the nav link and the hub tile
        assert 'href="/map"' not in page


@pytest.mark.parametrize("pilot", [None, Pilot(), Pilot(frozenset({"d" * 32}))],
                         ids=["vtt-off", "list-unset", "other-campaign"])
def test_otherwise_the_map_stays(hub, pilot):
    page = hub(pilot)
    assert "/vtt/gm" not in page and 'href="/map"' in page


def test_a_player_never_gets_the_tabletop_nav_link(hub):
    page = hub(EVERYONE, gm=False)
    nav = page[page.index("<nav"):page.index("</nav>")]
    assert "/vtt/gm" not in nav


def test_the_map_route_is_still_registered_as_the_fallback():
    assert any(rule.rule == "/map" for rule in app.app.url_map.iter_rules())
