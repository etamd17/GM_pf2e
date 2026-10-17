"""Serving the VTT client (M4a), behind VTT_ENABLED like everything VTT.

- ``GET /vtt/gm?campaignId=&sceneId=``  the GM workspace. Needs the member
  session and GM of that campaign. The page carries a boot JSON (campaign,
  scene to open, the app's CSRF token for the Socket.IO handshake).
- ``GET /vtt/table``                    the table display. Public shell: it
  holds no data. With no valid cookie the client shows pairing.
- ``GET /vtt/assets/<file>``            the built bundle's asset files.
- ``GET /vtt/api/scenes?campaignId=``   the GM's scene list (id, name,
  published, on the table). Needs GM of that campaign.

The bundle is built by Node and never committed (ADR-0003). It is read from
``dist_dir`` (``VTT_DIST_DIR``, default ``frontend/vtt/dist``). Without a build
the pages answer 503 rather than serve something half-made.
"""

from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, Response, abort, jsonify, request, send_from_directory
from sqlalchemy import select

from core.persistence.database import Database
from vtt.storage.models import VttScene, VttTablePointer
from vtt.transport import outage

ID_MAX = 128
ASSET_SUFFIXES = {".js", ".css", ".woff", ".woff2", ".ttf", ".png", ".svg", ".webp", ".jpg", ".jpeg"}


def _boot_script(data: dict) -> str:
    # Never executed (type application/json), but it sits inside HTML, so the
    # characters that could end the element or start markup are escaped.
    text = json.dumps(data, separators=(",", ":"))
    text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f'<script id="vtt-boot" type="application/json">{text}</script>'


def _valid_id(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= ID_MAX


def make_pages_blueprint(*, db: Database, host, dist_dir: Path, on_change=lambda campaign_id: None,
                         legacy_source=None) -> Blueprint:
    """``legacy_source(campaign_id) -> (scenes, table_scene_id)`` reads the
    legacy /map store for the import route (core.scenes in the real app)."""
    bp = Blueprint("vtt_pages", __name__)

    def built(name: str) -> str | None:
        path = dist_dir / name
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def not_built():
        return Response("VTT client not built.", status=503, mimetype="text/plain")

    def gm_of(campaign_id) -> str | None:
        """The member id when the caller is GM of this campaign, else None."""
        user_id = host.current_member_id()
        if not user_id or not _valid_id(campaign_id):
            return None
        with db.session() as session:
            return user_id if host.is_campaign_gm(session, user_id, campaign_id) else None

    def scenes_of(session, campaign_id: str):
        return session.execute(
            select(VttScene).where(VttScene.campaign_id == campaign_id).order_by(VttScene.name, VttScene.id)
        ).scalars().all()

    @bp.get("/vtt/gm")
    def gm_page():
        campaign_id = request.args.get("campaignId")
        member_id = gm_of(campaign_id)
        if member_id is None:
            abort(403)
        html = built("index.html")
        if html is None:
            return not_built()
        requested = request.args.get("sceneId")
        with db.session() as session:
            ids = [s.id for s in scenes_of(session, campaign_id)]
            pointer = session.get(VttTablePointer, campaign_id)
        if requested in ids:
            scene_id = requested
        elif pointer is not None and pointer.scene_id in ids:
            scene_id = pointer.scene_id
        else:
            scene_id = ids[0] if ids else None
        boot = _boot_script({"campaignId": campaign_id, "sceneId": scene_id, "csrf": host.csrf_token(),
                             # The stable member id: commands saved when a session ends
                             # are resent only by the same member (handoff 043). Not
                             # the CSRF token, which changes on reauthentication.
                             "memberId": member_id})
        response = Response(html.replace("</head>", boot + "</head>", 1), mimetype="text/html")
        response.headers["Cache-Control"] = "no-store"
        return response

    @bp.get("/vtt/table")
    def table_page():
        html = built("table.html")
        if html is None:
            return not_built()
        response = Response(html, mimetype="text/html")
        response.headers["Cache-Control"] = "no-store"
        return response

    @bp.get("/vtt/assets/<path:filename>")
    def assets(filename):
        if not dist_dir.is_dir():
            return not_built()
        # The build puts every asset at the root of dist/ (vite.config.ts,
        # assetsDir ''), beside the HTML entries. Only a single path segment
        # with an asset extension is served: never the HTML (the GM page must
        # go through its boot and authorization), never .vite/manifest.json.
        if "/" in filename or "\\" in filename or Path(filename).suffix.lower() not in ASSET_SUFFIXES:
            abort(404)
        # send_from_directory also refuses anything outside dist/. Built file
        # names carry a content hash, so they can be cached for good.
        response = send_from_directory(dist_dir, filename, max_age=31_536_000)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @bp.get("/vtt/api/scenes")
    def scene_list():
        campaign_id = request.args.get("campaignId")
        if gm_of(campaign_id) is None:
            return jsonify({"error": "unauthorized"}), 403
        with db.session() as session:
            pointer = session.get(VttTablePointer, campaign_id)
            on_table = pointer.scene_id if pointer is not None else None
            rows = scenes_of(session, campaign_id)
            return jsonify({"scenes": [{"id": s.id, "name": s.name, "published": bool(s.published),
                                        "onTable": s.id == on_table} for s in rows]})

    @bp.post("/vtt/api/import-legacy")
    def import_legacy():
        """Bring this campaign's /map scenes into the VTT (M5). ``apply`` false
        (the default) is a dry run: the report says what would be written and
        what cannot come across. Applied, live GMs and tables see it arrive."""
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        campaign_id = body.get("campaignId")
        if gm_of(campaign_id) is None or not host.csrf_valid(request.headers.get("X-CSRF-Token")):
            return jsonify({"error": "unauthorized"}), 403
        if legacy_source is None:
            return jsonify({"error": "no legacy store"}), 404
        from vtt.importer.legacy_scenes import import_campaign
        scenes, table_scene_id = legacy_source(campaign_id)
        apply = body.get("apply") is True
        report = import_campaign(db, campaign_id, scenes, table_scene_id, apply=apply)
        if apply:
            on_change(campaign_id)
        return jsonify(report)

    return outage.install(bp)
