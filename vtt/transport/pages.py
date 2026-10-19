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
import re
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


# E-1: a turn is expected to be a tracker combatant's (vtt.live ids).
COMBATANT_ID = re.compile(r"cmb_[A-Za-z0-9_-]{1,124}")
# E-1: the tracker's turn token (app._vtt_turn_token; GmActor.turnToken).
TURN_TOKEN = re.compile(r"[A-Za-z0-9-]{1,64}")
# E-2: a condition name, as the actor mirror carries it (vtt.live.CONDITION_NAME).
CONDITION_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")


# E-3: a bestiary reference (server-issued; a library path or a Foundry id),
# and a client request id.
BESTIARY_REF = re.compile(r"[^\x00-\x1f\x7f]{1,240}")
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")


def _small_int(value, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= high


def _valid_id(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= ID_MAX


def make_pages_blueprint(*, db: Database, host, dist_dir: Path, on_change=lambda campaign_id: None,
                         legacy_source=None, legacy_asset=None, asset_backend=None) -> Blueprint:
    """``legacy_source(campaign_id) -> (scenes, table_scene_id)`` reads the
    legacy /map store for the import route (core.scenes in the real app);
    ``legacy_asset(campaign_id, filename) -> bytes | None`` reads one of its
    image files, which the import stores in ``asset_backend`` (M6)."""
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

    def campaign_system(campaign_id: str) -> str:
        lookup = getattr(host, "campaign_system", None)
        try:
            system = lookup(campaign_id) if lookup else None
        except Exception:
            system = None
        return system if system in ("pf2e", "cosmere", "dnd5e") else "pf2e"

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
                             "memberId": member_id,
                             # The game system decides how the ruler counts distance (beta-1).
                             "system": campaign_system(campaign_id)})
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

    TURN_STATUS = {"ok": 200, "stale": 409, "not-live": 409, "no-encounter": 409, "unsupported": 422,
                   "blocked": 422}

    @bp.post("/vtt/api/turn")
    def turn():
        """Next or previous turn from the GM page (E-1), through the tracker.
        The body names what the page showed (the current combatant and round);
        anything else is refused with the current values, never acted on."""
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        campaign_id = body.get("campaignId")
        if gm_of(campaign_id) is None or not host.csrf_valid(request.headers.get("X-CSRF-Token")):
            return jsonify({"error": "unauthorized"}), 403
        direction = body.get("direction")
        actor_id = body.get("expectedActorId")
        expected_round = body.get("expectedRound")
        expected_token = body.get("turnToken")
        if (direction not in ("next", "prev") or not isinstance(actor_id, str) or not COMBATANT_ID.fullmatch(actor_id)
                or not isinstance(expected_token, str) or not TURN_TOKEN.fullmatch(expected_token)
                or not isinstance(expected_round, int) or isinstance(expected_round, bool)
                or not 0 <= expected_round <= 100000):
            return jsonify({"error": "bad request"}), 400
        step = getattr(host, "turn_step", None)
        code, detail = (step(campaign_id, direction, actor_id, expected_round, expected_token) if step
                        else ("unsupported", {}))
        status = TURN_STATUS.get(code, 422)
        return jsonify({"outcome": code, **(detail or {})}), status

    CONDITION_STATUS = {"ok": 200, "stale": 409, "not-live": 409, "not-in-encounter": 409,
                        "unsupported": 422, "invalid": 422, "blocked": 422}

    @bp.post("/vtt/api/condition")
    def condition():
        """Add, lower or remove a condition from the GM page (E-2), through the
        tracker's engine for the campaign's system. The body names the value the
        page showed (0 absent, 1 a flag, else the value); anything else is
        refused with the current value, never acted on."""
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        campaign_id = body.get("campaignId")
        if gm_of(campaign_id) is None or not host.csrf_valid(request.headers.get("X-CSRF-Token")):
            return jsonify({"error": "unauthorized"}), 403
        actor_id = body.get("actorId")
        name = body.get("condition")
        operation = body.get("operation")
        expected = body.get("expectedValue")
        rounds = body.get("rounds", 0)
        if (not isinstance(actor_id, str) or not COMBATANT_ID.fullmatch(actor_id)
                or not isinstance(name, str) or not CONDITION_NAME.fullmatch(name)
                or operation not in ("add", "decrease", "remove")
                or not _small_int(expected, 99) or not _small_int(rounds, 100)):
            return jsonify({"error": "bad request"}), 400
        change = getattr(host, "condition_change", None)
        code, detail = (change(campaign_id, actor_id, name, operation, expected, rounds) if change
                        else ("unsupported", {}))
        return jsonify({"outcome": code, **(detail or {})}), CONDITION_STATUS.get(code, 422)

    BESTIARY_STATUS = {"ok": 200, "stale": 409, "not-live": 409, "not-found": 404, "unsupported": 422,
                       "blocked": 422}

    @bp.get("/vtt/api/bestiary")
    def bestiary_search():
        """Search the campaign's bestiary (E-3): ?campaignId=&q=."""
        campaign_id = request.args.get("campaignId")
        if gm_of(campaign_id) is None:
            return jsonify({"error": "unauthorized"}), 403
        query = request.args.get("q", "")
        if len(query) > 80:
            return jsonify({"error": "bad request"}), 400
        search = getattr(host, "bestiary_search", None)
        code, detail = search(campaign_id, query) if search else ("unsupported", {})
        return jsonify({"outcome": code, **(detail or {})}), BESTIARY_STATUS.get(code, 422)

    @bp.post("/vtt/api/bestiary/add")
    def bestiary_add():
        """Add a bestiary creature to the live encounter (E-3), through the
        tracker. The page places the linked token itself. requestId makes a
        retry harmless."""
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        campaign_id = body.get("campaignId")
        if gm_of(campaign_id) is None or not host.csrf_valid(request.headers.get("X-CSRF-Token")):
            return jsonify({"error": "unauthorized"}), 403
        ref = body.get("ref")
        request_id = body.get("requestId")
        expected = body.get("encounterToken")
        if (not isinstance(ref, str) or not BESTIARY_REF.fullmatch(ref)
                or not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id)
                or not isinstance(expected, str) or not TURN_TOKEN.fullmatch(expected)):
            return jsonify({"error": "bad request"}), 400
        add = getattr(host, "bestiary_add", None)
        code, detail = add(campaign_id, ref, request_id, expected) if add else ("unsupported", {})
        return jsonify({"outcome": code, **(detail or {})}), BESTIARY_STATUS.get(code, 422)

    AREA_STATUS = {"ok": 200, "stale": 409, "not-live": 409, "unsupported": 422, "invalid": 422, "blocked": 422}
    AREA_PARAMS = ("amount", "damageType", "nonlethal", "save", "dc", "secret")

    @bp.post("/vtt/api/area-effect")
    def area_effect():
        """Apply an area effect to the GM's reviewed target list (E-5), through
        the tracker. requestId makes a retry harmless; encounterToken binds it
        to the encounter the page showed."""
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        campaign_id = body.get("campaignId")
        if gm_of(campaign_id) is None or not host.csrf_valid(request.headers.get("X-CSRF-Token")):
            return jsonify({"error": "unauthorized"}), 403
        request_id = body.get("requestId")
        token = body.get("encounterToken")
        action = body.get("action")
        actor_ids = body.get("actorIds")
        params = {key: body[key] for key in AREA_PARAMS if key in body}
        if (not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id)
                or not isinstance(token, str) or not TURN_TOKEN.fullmatch(token)
                or action not in ("damage", "heal", "save")
                or not isinstance(actor_ids, list) or not 1 <= len(actor_ids) <= 50
                or not all(isinstance(a, str) and COMBATANT_ID.fullmatch(a) for a in actor_ids)
                or not all(isinstance(v, (int, str, bool)) and (not isinstance(v, str) or len(v) <= 40)
                           for v in params.values())):
            return jsonify({"error": "bad request"}), 400
        apply = getattr(host, "area_apply", None)
        code, detail = (apply(campaign_id, request_id, token, action, actor_ids, params) if apply
                        else ("unsupported", {}))
        return jsonify({"outcome": code, **(detail or {})}), AREA_STATUS.get(code, 422)

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
        report = import_campaign(db, campaign_id, scenes, table_scene_id, apply=apply,
                                 read_asset=legacy_asset, asset_backend=asset_backend)
        if apply:
            on_change(campaign_id)
        return jsonify(report)

    return outage.install(bp)
