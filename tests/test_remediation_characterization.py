"""Characterization gates for the staged remediation program.

These tests intentionally do not change application behavior.  Passing tests
prevent the known ambient-state coupling from growing.  Conditional xfails
describe accepted security contracts after environment validation; missing
dependencies, import failures, and setup failures remain ordinary failures.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap

import pytest


_REPO = Path(__file__).resolve().parent.parent
_APP = _REPO / "app.py"

# Audited at commit 7aed6730.  These are ceilings, not required values: removing
# a legacy reference passes, while adding another dependency on ambient campaign
# state fails and requires an architectural review.
_AMBIENT_REFERENCE_CEILINGS = {
    "ACTIVE_CAMPAIGN_ID": 19,
    "PARTY_LIBRARY": 205,
    "ACTIVE_ENCOUNTER": 208,
    "PARTY_DIR": 28,
    "ENCOUNTER_DIR": 28,
}


def _run_isolated(body: str) -> subprocess.CompletedProcess[str]:
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=_REPO,
        timeout=90,
    )


@pytest.mark.parametrize("name,ceiling", sorted(_AMBIENT_REFERENCE_CEILINGS.items()))
def test_ambient_campaign_reference_count_does_not_increase(name: str, ceiling: int):
    source = _APP.read_text(encoding="utf-8")
    count = len(re.findall(rf"\b{re.escape(name)}\b", source))
    assert count <= ceiling, (
        f"{name} references grew from the audited ceiling {ceiling} to {count}; "
        "new code must use explicit campaign context/repositories instead"
    )


def test_anonymous_requests_cannot_read_player_or_party_surfaces():
    result = _run_isolated(
        """
        import json
        import os
        import tempfile

        os.environ["DATA_DIR"] = tempfile.mkdtemp()
        os.environ["GM_PASSWORD"] = ""

        import app as application

        admin = application.app.test_client()
        response = admin.post(
            "/setup",
            data={"username": "admin", "password": "secret1", "display_name": "Admin"},
        )
        assert response.status_code == 302, response.status_code

        anonymous = application.app.test_client()
        statuses = {
            "/player": anonymous.get("/player").status_code,
            "/api/player_state": anonymous.get("/api/player_state").status_code,
            "/api/gm_party_state": anonymous.get("/api/gm_party_state").status_code,
        }
        print(json.dumps(statuses, sort_keys=True))
        """
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    statuses = json.loads(result.stdout.strip().splitlines()[-1])
    exposed = {
        path: code
        for path, code in statuses.items()
        if code not in {302, 401, 403, 404}
    }
    if exposed:
        pytest.xfail(
            "ADR-0001 containment contract; implemented in the next remediation PR"
        )
    assert not exposed


def test_viewing_a_sheet_does_not_change_the_session_actor():
    result = _run_isolated(
        """
        import json
        import os
        import tempfile

        os.environ["DATA_DIR"] = tempfile.mkdtemp()
        os.environ["GM_PASSWORD"] = ""

        import app as application
        from core import auth, campaigns

        admin_client = application.app.test_client()
        response = admin_client.post(
            "/setup",
            data={"username": "admin", "password": "secret1", "display_name": "Admin"},
        )
        assert response.status_code == 302, response.status_code
        admin = auth.get_user_by_username("admin")
        player = auth.create_user("player", "secret2", display_name="Player")
        campaign = campaigns.create_campaign("Identity test", "pf2e", admin["id"])
        campaigns.add_member(campaign["id"], player["id"], "player")

        client = application.app.test_client()
        response = client.post("/login", data={"username": "player", "password": "secret2"})
        assert response.status_code == 302, response.status_code
        with client.session_transaction() as session:
            session["active_campaign_id"] = campaign["id"]
            session.pop("player_name", None)

        # Isolate the route's identity behavior from the very large sheet render.
        application.PARTY_LIBRARY["Viewed Character"] = object()
        application.render_template = lambda *args, **kwargs: "sheet"
        response = client.get("/player/sheet/Viewed%20Character")
        assert response.status_code == 200, response.status_code

        with client.session_transaction() as session:
            print(json.dumps({"player_name": session.get("player_name")}, sort_keys=True))
        """
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    session_state = json.loads(result.stdout.strip().splitlines()[-1])
    player_name = session_state["player_name"]
    if player_name is not None:
        pytest.xfail(
            "ADR-0001 canonical actor contract; implemented with stable character IDs"
        )
    assert player_name is None
