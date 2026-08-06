"""Handout image uploads must persist on the data volume, not the app's
ephemeral static folder, so they survive a Railway redeploy.

Regression guard for the bug where ``/api/handout_upload`` wrote into
``static/uploads/handouts`` — a directory baked into the deploy image and
wiped on every push to main. The fix routes uploads to
``DATA_DIR/uploads/handouts`` (the persistent volume) and serves them from a
dedicated ``/handouts/<file>`` route.

``app.py`` resolves ``DATA_DIR`` from the environment at import time, so the
honest way to prove a file outlives the process is a real restart: this test
runs two short-lived subprocesses that share one ``DATA_DIR``. The first
uploads an image; the second (the simulated redeploy) must still serve it.
Self-contained and CI-safe — no party_data needed, only the repo's compendium.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Arbitrary bytes with a PNG-ish header; the route gates on extension + size,
# not content, so this stands in for a real image.
_PAYLOAD = b"\x89PNG\r\n\x1a\n" + b"handout-volume-persistence-payload" * 8

# Phase 1: upload + assert it landed on the volume (not the static dir),
# that the extension whitelist, uuid filename and 10 MB cap still hold, and
# that it serves back in-process. Writes the chosen filename to $H_FN.
_UPLOAD = textwrap.dedent(
    """
    import io, os, app
    app.app.config["TESTING"] = True
    c = app.app.test_client()
    payload = open(os.environ["H_PAYLOAD"], "rb").read()

    r = c.post("/api/handout_upload",
               data={"image": (io.BytesIO(payload), "my handout.png")},
               content_type="multipart/form-data")
    assert r.status_code == 200, (r.status_code, r.data)
    url = r.get_json()["url"]
    assert url.startswith("/handouts/"), url
    fn = url.rsplit("/", 1)[-1]

    stem, ext = os.path.splitext(fn)
    assert ext == ".png", fn                       # extension preserved
    assert len(stem) == 12 and stem.isalnum(), fn  # uuid hex filename preserved

    # Lives on the volume (under DATA_DIR), NOT in the ephemeral static folder.
    assert app.HANDOUTS_DIR.startswith(os.environ["DATA_DIR"]), app.HANDOUTS_DIR
    assert os.path.isfile(os.path.join(app.HANDOUTS_DIR, fn)), app.HANDOUTS_DIR
    assert not os.path.exists(
        os.path.join(app.BASE_DIR, "static", "uploads", "handouts", fn)
    ), "leaked into ephemeral static dir"

    g = c.get(url)
    assert g.status_code == 200 and g.data == payload, (g.status_code, len(g.data))

    # 10 MB per-file cap still rejects oversized uploads.
    big = c.post("/api/handout_upload",
                 data={"image": (io.BytesIO(b"\\0" * (10 * 1024 * 1024 + 1)), "big.png")},
                 content_type="multipart/form-data")
    assert big.status_code == 400, big.status_code

    # Extension whitelist still rejects non-images.
    bad = c.post("/api/handout_upload",
                 data={"image": (io.BytesIO(b"nope"), "evil.txt")},
                 content_type="multipart/form-data")
    assert bad.status_code == 400, bad.status_code

    open(os.environ["H_FN"], "w").write(fn)
    """
)

# Phase 2: a fresh process (the simulated restart) sharing the same DATA_DIR
# must still serve the file uploaded by phase 1.
_SERVE = textwrap.dedent(
    """
    import os, app
    app.app.config["TESTING"] = True
    c = app.app.test_client()
    fn = open(os.environ["H_FN"]).read().strip()
    payload = open(os.environ["H_PAYLOAD"], "rb").read()

    g = c.get("/handouts/" + fn)
    assert g.status_code == 200, g.status_code        # survived the restart
    assert g.data == payload, "served bytes differ after restart"

    # send_from_directory must still refuse to escape the handouts dir.
    bad = c.get("/handouts/..%2f..%2fapp.py")
    assert bad.status_code in (403, 404), bad.status_code
    """
)


def _run(code: str, data_dir: Path, fn_path: Path, payload_path: Path):
    env = dict(os.environ)
    env.update(
        DATA_DIR=str(data_dir),
        GM_PASSWORD="",  # empty password => the GM-access gate is a no-op
        PYTHONPATH=str(_REPO_ROOT),
        H_FN=str(fn_path),
        H_PAYLOAD=str(payload_path),
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_handout_survives_simulated_restart(tmp_path):
    data_dir = tmp_path / "volume"
    data_dir.mkdir()
    payload_path = tmp_path / "payload.bin"
    payload_path.write_bytes(_PAYLOAD)
    fn_path = tmp_path / "filename.txt"

    up = _run(_UPLOAD, data_dir, fn_path, payload_path)
    assert up.returncode == 0, f"upload phase failed:\nSTDOUT:\n{up.stdout}\nSTDERR:\n{up.stderr}"

    # The file physically exists on the (persistent) volume after phase 1.
    saved = fn_path.read_text().strip()
    assert (data_dir / "uploads" / "handouts" / saved).is_file()

    serve = _run(_SERVE, data_dir, fn_path, payload_path)
    assert serve.returncode == 0, f"serve-after-restart phase failed:\nSTDOUT:\n{serve.stdout}\nSTDERR:\n{serve.stderr}"
