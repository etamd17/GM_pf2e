"""Asset routes (M6, handoff 053).

- ``POST /vtt/api/assets?campaignId=...``: the campaign's GM uploads an image
  as the raw request body, with the page's CSRF token. The type is decided by
  the bytes. Answers the asset's description; the same bytes again answer the
  same asset (200 instead of 201).
- ``GET /vtt/media/<campaign>/<asset>``: the bytes. A GM member of that
  campaign may read any of its assets. A paired display may read only what the
  current table projection shows (``service.table_may_read``). Everyone else,
  and anything refused, gets the same 404, so the route does not reveal
  whether an asset exists. Not under ``/vtt/assets/``, which serves the client
  bundle.

Responses are private (authorized content), immutable (the id is the content
hash) and sandboxed, so an image URL opened directly can never run anything.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from flask import Blueprint, Response, abort, jsonify, request

from core.persistence.database import Database
from vtt.assets import service
from vtt.assets.backends import AssetBackend, AssetBackendError
from vtt.assets.images import MAX_BYTES, BadImage
from vtt.transport import outage

log = logging.getLogger(__name__)
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def make_assets_blueprint(*, db: Database, host, backend: AssetBackend,
                          table_campaign: Callable[[object], str | None]) -> Blueprint:
    """``table_campaign(session)`` is the campaign of the request's table
    credential when that grant stands, else None."""
    bp = Blueprint("vtt_assets", __name__)

    def gm_of(session, campaign_id: str) -> str | None:
        user_id = host.current_member_id()
        if not user_id:
            return None
        return user_id if host.is_campaign_gm(session, user_id, campaign_id) else None

    @bp.post("/vtt/api/assets")
    def upload():
        campaign_id = request.args.get("campaignId") or ""
        if not _ID.fullmatch(campaign_id) or not host.csrf_valid(request.headers.get("X-CSRF-Token")):
            return jsonify({"error": "unauthorized"}), 403
        with db.session() as session:
            user_id = gm_of(session, campaign_id)
        if user_id is None:
            return jsonify({"error": "unauthorized"}), 403
        if request.content_length is not None and request.content_length > MAX_BYTES:
            return jsonify({"error": "images must be 25 MB or smaller"}), 413
        data = request.stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return jsonify({"error": "images must be 25 MB or smaller"}), 413
        try:
            described, created = service.store(db, backend, campaign_id, user_id, data)
        except BadImage as refused:
            return jsonify({"error": str(refused)}), 400
        except AssetBackendError:
            log.warning("VTT asset backend unavailable on upload")
            return jsonify({"error": "temporarily-unavailable"}), 503, {"Retry-After": "5"}
        return jsonify({"asset": described}), 201 if created else 200

    @bp.get("/vtt/media/<campaign_id>/<asset_id>")
    def media(campaign_id: str, asset_id: str):
        if not _ID.fullmatch(campaign_id) or not _ID.fullmatch(asset_id):
            abort(404)
        with db.session() as session:
            allowed = gm_of(session, campaign_id) is not None or (
                table_campaign(session) == campaign_id and service.table_may_read(session, campaign_id, asset_id))
            if not allowed:
                abort(404)
            try:
                found = service.read(session, backend, campaign_id, asset_id)
            except AssetBackendError:
                log.warning("VTT asset backend unavailable on read")
                return jsonify({"error": "temporarily-unavailable"}), 503, {"Retry-After": "5"}
            if found is None:
                abort(404)
            row, data = found
            etag = row.sha256
        if request.if_none_match and etag in request.if_none_match:
            response = Response(status=304)
        else:
            response = Response(data, mimetype=row.media_type)
        response.headers["ETag"] = f'"{etag}"'
        response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        response.headers["Content-Disposition"] = "inline"
        return response

    return outage.install(bp)
