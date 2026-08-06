"""GM-hub declutter: Threads / Status / Notes were removed from the system nav
sets (and Notes from the player navs) + the GM-hub tiles. The routes themselves
stay live (unlinked) so nothing breaks and it's one-line reversible.
"""
from __future__ import annotations

import systems
import app


def test_nav_drops_threads_status_notes():
    for key in ('pf2e', 'cosmere'):
        ui = systems.get(key).ui
        gm_labels = {l.label for l in ui.gm_nav}
        player_labels = {l.label for l in ui.player_nav}
        for gone in ('Threads', 'Status', 'Notes'):
            assert gone not in gm_labels, f"{key} gm_nav still lists {gone}"
        assert 'Notes' not in player_labels, f"{key} player_nav still lists Notes"


def test_removed_routes_still_resolve():
    # Unlinked, not deleted — a bookmarked URL must still work.
    c = app.app.test_client()
    for path in ('/status', '/notes', '/gm/threads'):
        assert c.get(path).status_code in (200, 302), f"{path} 404'd"
