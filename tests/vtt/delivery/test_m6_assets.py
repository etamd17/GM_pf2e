"""M6: the asset store (handoff 053 item 1). Images are checked by their bytes,
stored by content hash, readable by the campaign's GM and, for a paired
display, only when the current table projection shows them; commands may only
reference assets the campaign holds."""

from __future__ import annotations

import io
import struct
import zlib

import pytest
from sqlalchemy import func, select

from vtt.assets import backends, images, service
from vtt.storage import writes as W
from vtt.storage.models import VttAsset

from .conftest import CSRF, add_grant
from .test_m1_read_path import seed_scene, subscribe_gm
from .test_m2_commands import TOKEN, expect, send

ORIGIN = "https://localhost"


def picture(fmt="PNG", size=(4, 3), color=(200, 30, 30)) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, fmt)
    return out.getvalue()


def png_claiming(width: int, height: int) -> bytes:
    """A PNG whose header declares these dimensions (no pixels behind them)."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


def upload(stack, data, campaign="camp_a", csrf=CSRF, content_type="image/png"):
    return stack.app.test_client().post(f"/vtt/api/assets?campaignId={campaign}", data=data,
                                        headers={"X-CSRF-Token": csrf, "Content-Type": content_type},
                                        base_url=ORIGIN)


def fetch(stack, campaign, asset, table_credential=None, member=True, **headers):
    client = stack.app.test_client()
    if table_credential:
        client.set_cookie("vtt_table", table_credential)
    previous = stack.host.member_id
    if not member:
        stack.host.member_id = None
    try:
        return client.get(f"/vtt/media/{campaign}/{asset}", base_url=ORIGIN, headers=headers)
    finally:
        stack.host.member_id = previous


def stored(stack, data=None, campaign="camp_a"):
    response = upload(stack, data or picture(), campaign)
    assert response.status_code in (200, 201), response.get_json()
    return response.get_json()["asset"]["id"]


# --- what counts as an image -----------------------------------------------------------------

@pytest.mark.parametrize("fmt,media", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")])
def test_png_jpeg_and_webp_are_accepted_by_their_bytes(fmt, media):
    info = images.inspect(picture(fmt, (40, 30)))
    assert (info.media_type, info.width, info.height) == (media, 40, 30)


@pytest.mark.parametrize("data,reason", [
    (b"", "empty"),
    (b"GIF89a" + b"\x00" * 40, "PNG, JPEG or WebP"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "PNG, JPEG or WebP"),
    (b"\x89PNG\r\n\x1a\n" + b"garbage" * 10, "not a readable"),
])
def test_anything_else_is_refused(data, reason):
    with pytest.raises(images.BadImage, match=reason):
        images.inspect(data)


def test_a_declared_giant_is_refused_from_its_header_alone():
    with pytest.raises(images.BadImage, match="16384"):
        images.inspect(png_claiming(20000, 10))


def test_a_truncated_file_is_refused():
    with pytest.raises(images.BadImage):
        images.inspect(picture("PNG", (64, 64))[:-30])


def test_too_large_a_file_is_refused(monkeypatch):
    monkeypatch.setattr(images, "MAX_BYTES", 100)
    with pytest.raises(images.BadImage, match="25 MB"):
        images.inspect(picture("PNG", (64, 64)) + b"\x00" * 200)


# --- backends --------------------------------------------------------------------------------

def test_the_local_backend_refuses_any_key_that_is_not_campaign_and_hash(tmp_path):
    store = backends.LocalBackend(tmp_path)
    for bad in ("../x/" + "a" * 64, "camp_a/../../etc", "camp_a/" + "A" * 64, "camp a/" + "a" * 64):
        with pytest.raises(ValueError):
            store.put(bad, b"x", "image/png")
    store.put("camp_a/" + "a" * 64, b"bytes", "image/png")
    assert store.get("camp_a/" + "a" * 64) == b"bytes"
    assert store.get("camp_a/" + "b" * 64) is None


def test_backend_selection_from_the_environment(tmp_path):
    assert isinstance(backends.from_env({"DATA_DIR": str(tmp_path)}), backends.LocalBackend)
    with pytest.raises(ValueError, match="needs"):
        backends.from_env({"VTT_ASSETS_BACKEND": "s3"})
    with pytest.raises(ValueError):
        backends.from_env({"VTT_ASSETS_BACKEND": "ftp"})
    s3 = backends.from_env({"VTT_ASSETS_BACKEND": "s3", "VTT_ASSETS_S3_ENDPOINT": "http://minio:9000",
                            "VTT_ASSETS_S3_BUCKET": "vtt-assets", "VTT_ASSETS_S3_ACCESS_KEY": "k",
                            "VTT_ASSETS_S3_SECRET_KEY": "s"})
    assert isinstance(s3, backends.S3Backend)


def test_sigv4_signature_matches_the_published_aws_example():
    # The GET Object example from the AWS SigV4 documentation ("Example: GET
    # Object"), with the documented credentials, date and expected signature.
    # Path-style addressing changes the canonical URI, so the canonical request
    # is rebuilt here from the backend's own pieces and compared step by step.
    import datetime as dt
    import hashlib
    import hmac
    secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    day, region = "20130524", "us-east-1"
    key = f"AWS4{secret}".encode()
    for part in (day, region, "s3", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    # The documented signing key for this date/region/service, as hex.
    assert key.hex() == "dbb893acc010964918f1fd433add87c70e8b0db6be30c1fbeafefa5ec6ba8378"
    backend = backends.S3Backend("http://examplebucket.s3.amazonaws.com", "examplebucket",
                                 "AKIAIOSFODNN7EXAMPLE", secret,
                                 now=lambda: dt.datetime(2013, 5, 24, tzinfo=dt.timezone.utc))
    request = backend._signed("GET", "camp_a/" + "a" * 64, b"", {})
    auth = request.get_header("Authorization")
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, ")
    assert "SignedHeaders=host;x-amz-content-sha256;x-amz-date" in auth
    assert request.get_header("X-amz-content-sha256") == hashlib.sha256(b"").hexdigest()


# --- upload ----------------------------------------------------------------------------------

def test_the_gm_uploads_and_the_same_bytes_are_the_same_asset(stack):
    data = picture("PNG", (40, 30))
    first = upload(stack, data)
    assert first.status_code == 201
    asset = first.get_json()["asset"]
    assert asset["id"].startswith("asset_") and (asset["width"], asset["height"]) == (40, 30)
    assert asset["mediaType"] == "image/png" and asset["byteSize"] == len(data)
    again = upload(stack, data, content_type="image/jpeg")     # the claimed type is ignored
    assert again.status_code == 200 and again.get_json()["asset"] == asset
    with stack.db.session() as session:
        assert session.execute(select(func.count()).select_from(VttAsset)).scalar_one() == 1
        row = session.get(VttAsset, ("camp_a", asset["id"]))
    assert stack.assets.get(backends.key_for("camp_a", row.sha256)) == data


def test_upload_needs_the_campaign_gm_and_csrf(stack):
    assert upload(stack, picture(), csrf="wrong").status_code == 403
    assert upload(stack, picture(), campaign="camp_b").status_code == 403
    assert upload(stack, picture(), campaign="../x").status_code == 403
    stack.host.member_id = None
    assert upload(stack, picture()).status_code == 403


def test_a_refused_file_says_why_and_stores_nothing(stack):
    response = upload(stack, b"GIF89a" + b"\x00" * 40)
    assert response.status_code == 400 and "PNG, JPEG or WebP" in response.get_json()["error"]
    with stack.db.session() as session:
        assert session.execute(select(func.count()).select_from(VttAsset)).scalar_one() == 0


def test_an_oversized_upload_is_refused_before_it_is_read(stack, monkeypatch):
    from vtt.transport import assets as route
    monkeypatch.setattr(route, "MAX_BYTES", 10)
    assert upload(stack, picture()).status_code == 413


def test_a_backend_failure_is_a_retryable_503_and_records_nothing(stack, monkeypatch):
    def broken(*_a, **_k):
        raise backends.AssetBackendError("down")
    monkeypatch.setattr(stack.assets, "put", broken)
    response = upload(stack, picture())
    assert response.status_code == 503 and response.headers["Retry-After"] == "5"
    with stack.db.session() as session:
        assert session.execute(select(func.count()).select_from(VttAsset)).scalar_one() == 0


# --- reading ---------------------------------------------------------------------------------

def test_the_gm_reads_any_asset_of_the_campaign_with_safe_headers(stack):
    data = picture()
    asset = stored(stack, data)
    response = fetch(stack, "camp_a", asset)
    assert response.status_code == 200 and response.data == data
    assert response.mimetype == "image/png"
    assert response.headers["Cache-Control"] == "private, max-age=31536000, immutable"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response.headers["Content-Security-Policy"]
    etag = response.headers["ETag"]
    assert fetch(stack, "camp_a", asset, **{"If-None-Match": etag}).status_code == 304


def test_nobody_else_can_tell_whether_an_asset_exists(stack):
    asset = stored(stack)
    assert fetch(stack, "camp_a", asset, member=False).status_code == 404
    assert fetch(stack, "camp_a", "asset_" + "0" * 40).status_code == 404
    stack.host.gms.add(("user_gm", "camp_b"))
    assert fetch(stack, "camp_b", asset).status_code == 404       # another campaign's id
    stack.host.member_id = "user_other"
    assert fetch(stack, "camp_a", asset).status_code == 404       # not the GM


def _table_scene(stack, *, background=None, art=None, hidden_art=None):
    seed_scene(stack.db)
    with stack.db.transaction() as session:
        from vtt.storage.models import VttEntity, VttScene
        session.get(VttScene, ("camp_a", "scene_a")).background_asset_id = background
        if art:
            hero = session.get(VttEntity, ("camp_a", "scene_a", "token", "tok_hero"))
            hero.data = {**hero.data, "artAssetId": art}
        if hidden_art:
            ambush = session.get(VttEntity, ("camp_a", "scene_a", "token", "tok_ambush"))
            ambush.data = {**ambush.data, "artAssetId": hidden_art}


def test_a_display_reads_only_what_the_table_projection_shows(stack):
    background = stored(stack, picture(color=(1, 2, 3)))
    art = stored(stack, picture(color=(4, 5, 6)))
    secret = stored(stack, picture(color=(7, 8, 9)))
    unrelated = stored(stack, picture(color=(10, 11, 12)))
    _table_scene(stack, background=background, art=art, hidden_art=secret)
    table = add_grant(stack.db)
    assert fetch(stack, "camp_a", background, table, member=False).status_code == 200
    assert fetch(stack, "camp_a", art, table, member=False).status_code == 200
    assert fetch(stack, "camp_a", secret, table, member=False).status_code == 404     # hidden token's art
    assert fetch(stack, "camp_a", unrelated, table, member=False).status_code == 404  # not on the table


def test_a_display_loses_access_when_the_table_moves_or_the_grant_ends(stack):
    background = stored(stack)
    _table_scene(stack, background=background)
    table = add_grant(stack.db)
    assert fetch(stack, "camp_a", background, table, member=False).status_code == 200
    W.set_table_pointer(stack.db, "camp_a", None)
    assert fetch(stack, "camp_a", background, table, member=False).status_code == 404
    W.set_table_pointer(stack.db, "camp_a", "scene_a")
    revoked = add_grant(stack.db, revoked=True)
    assert fetch(stack, "camp_a", background, revoked, member=False).status_code == 404
    other = add_grant(stack.db, "camp_b")
    assert fetch(stack, "camp_a", background, other, member=False).status_code == 404


def test_an_unpublished_table_scene_shows_the_display_nothing(stack):
    background = stored(stack)
    _table_scene(stack, background=background)
    from vtt.storage.models import VttScene
    with stack.db.transaction() as session:
        session.get(VttScene, ("camp_a", "scene_a")).published = False
    assert fetch(stack, "camp_a", background, add_grant(stack.db), member=False).status_code == 404


# --- commands may reference only the campaign's own assets -----------------------------------

def test_token_art_and_backgrounds_must_be_assets_the_campaign_holds(stack):
    seed_scene(stack.db)
    gm, _ = subscribe_gm(stack)
    mine = stored(stack)
    stack.host.gms.add(("user_gm", "camp_b"))
    theirs = stored(stack, picture(color=(9, 9, 9)), campaign="camp_b")
    _, ack, _ = send(stack, gm, "token.create", {**TOKEN, "id": "tok_a", "artAssetId": "asset_" + "1" * 40})
    assert ack["outcome"] == "rejected" and ack["code"] == "rule-violation"
    _, ack, _ = send(stack, gm, "token.create", {**TOKEN, "id": "tok_b", "artAssetId": theirs})
    assert ack["code"] == "rule-violation"
    _, ack, _ = send(stack, gm, "token.create", {**TOKEN, "id": "tok_c", "artAssetId": mine})
    assert ack["outcome"] == "applied"
    _, ack, _ = send(stack, gm, "token.update", {"id": "tok_hero", "artAssetId": theirs},
                     expected=expect(stack, "token", "tok_hero"))
    assert ack["code"] == "rule-violation"
    _, ack, _ = send(stack, gm, "scene.update", {"backgroundAssetId": mine}, expected=expect(stack, "scene", "scene_a"))
    assert ack["outcome"] == "applied"
    _, ack, _ = send(stack, gm, "scene.update", {"backgroundAssetId": None}, expected=expect(stack, "scene", "scene_a"))
    assert ack["outcome"] == "applied"       # clearing needs no asset


def test_store_is_idempotent_under_a_repeat_and_ids_are_hash_derived(stack):
    data = picture()
    first, created = service.store(stack.db, stack.assets, "camp_a", "user_gm", data)
    again, created_again = service.store(stack.db, stack.assets, "camp_a", "user_gm", data)
    assert created and not created_again and first == again
    import hashlib
    assert first["id"] == "asset_" + hashlib.sha256(data).hexdigest()[:40]


# --- the /map import brings images across (handoff 053 item 2) -------------------------------

def _legacy_with_images(stack, *, art_bytes=None, background_bytes=None):
    from .test_m5_import import full_scene
    scene = full_scene()
    scene["background"] = {"filename": "abc123.png"}
    hero = scene["tokens"][0]
    hero["image"] = "/api/scenes/abc123/tokens/tok1/image?v=123"
    if background_bytes is not None:
        stack.legacy_files[("camp_a", "abc123.png")] = background_bytes
    if art_bytes is not None:
        stack.legacy_files[("camp_a", "abc123_token_tok1.jpg")] = art_bytes
    return scene


def _import(stack, scene, apply):
    from vtt.importer import legacy_scenes as importer
    return importer.import_campaign(stack.db, "camp_a", [scene], "abc123", apply=apply,
                                    read_asset=lambda cid, name: stack.legacy_files.get((cid, name)),
                                    asset_backend=stack.assets)["scenes"][0]


def _asset_count(stack):
    with stack.db.session() as session:
        return session.execute(select(func.count()).select_from(VttAsset)).scalar_one()


def test_the_import_brings_a_background_and_uploaded_token_art_across(stack):
    background, art = picture("PNG", (70, 50)), picture("JPEG", (32, 32))
    scene = _legacy_with_images(stack, art_bytes=art, background_bytes=background)
    dry = _import(stack, scene, apply=False)
    assert dry["images"] == 2 and _asset_count(stack) == 0            # the dry run stores nothing
    assert not any("background" in d for d in dry["dropped"])
    assert sum("token art" in d for d in dry["dropped"]) == 1          # tok2's "spy.png" is not /map art
    applied = _import(stack, scene, apply=True)
    assert applied["status"] == "imported" and _asset_count(stack) == 2
    from vtt.storage.models import VttEntity, VttScene
    with stack.db.session() as session:
        bg = session.get(VttScene, ("camp_a", "legacy_abc123")).background_asset_id
        hero_art = session.get(VttEntity, ("camp_a", "legacy_abc123", "token", "legacy_tok1")).data["artAssetId"]
    assert bg == service.asset_id(__import__("hashlib").sha256(background).hexdigest())
    assert hero_art == service.asset_id(__import__("hashlib").sha256(art).hexdigest())
    # the paired display can read both: the scene is the table scene and the token is visible
    table = add_grant(stack.db)
    assert fetch(stack, "camp_a", bg, table, member=False).data == background
    assert fetch(stack, "camp_a", hero_art, table, member=False).data == art
    again = _import(stack, scene, apply=True)                          # re-import: same assets
    assert again["status"] == "imported" and _asset_count(stack) == 2


def test_missing_or_unreadable_legacy_images_are_reported_not_referenced(stack):
    scene = _legacy_with_images(stack, art_bytes=b"not an image", background_bytes=None)
    report = _import(stack, scene, apply=True)
    assert report["status"] == "imported"
    assert any(d.startswith("background image (file missing") for d in report["dropped"])
    assert any("token art on 1 token(s) (file missing or unreadable)" in d for d in report["dropped"])
    from vtt.storage.models import VttEntity, VttScene
    with stack.db.session() as session:
        assert session.get(VttScene, ("camp_a", "legacy_abc123")).background_asset_id is None
        assert session.get(VttEntity, ("camp_a", "legacy_abc123", "token", "legacy_tok1")).data["artAssetId"] is None
    assert _asset_count(stack) == 0


def test_art_whose_url_names_another_token_is_not_read(stack):
    scene = _legacy_with_images(stack, art_bytes=picture())
    scene["tokens"][0]["image"] = "/api/scenes/other_scene/tokens/tok1/image"
    report = _import(stack, scene, apply=False)
    assert "images" not in report            # no background file either: nothing would be read
    assert any("not an image uploaded to /map" in d for d in report["dropped"])


def test_an_asset_store_outage_refuses_the_scene_whole(stack, monkeypatch):
    scene = _legacy_with_images(stack, art_bytes=picture("JPEG"), background_bytes=picture())
    def broken(*_a, **_k):
        raise backends.AssetBackendError("down")
    monkeypatch.setattr(stack.assets, "put", broken)
    report = _import(stack, scene, apply=True)
    assert report["status"] == "refused" and "asset store is unavailable" in report["reason"]
    from vtt.storage.models import VttScene
    with stack.db.session() as session:
        assert session.get(VttScene, ("camp_a", "legacy_abc123")) is None


def test_the_import_route_reads_the_legacy_files(stack):
    from .test_m5_import import post
    scene = _legacy_with_images(stack, art_bytes=picture("JPEG"), background_bytes=picture())
    stack.legacy["camp_a"] = ([scene], "abc123")
    report = post(stack, {"campaignId": "camp_a", "apply": True}).get_json()
    assert report["scenes"][0]["images"] == 2 and _asset_count(stack) == 2
