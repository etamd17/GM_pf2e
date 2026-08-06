"""Chronicle cross-campaign routing bug (2026-07-29 fix).

Regression coverage for: CHRONICLE_DIR is a process global bound to the
server-wide LIVE SLOT (_bind_campaign_paths), but _active_campaign_id() is, in
account mode, deliberately the browsing user's OWN campaign selection and
NEVER the live slot. Before this fix, /api/chronicle/publish always wrote to
CHRONICLE_DIR (the live slot) and every reader resolved through the same
global -- so a GM publishing to their own campaign could silently write into
whichever OTHER campaign happened to hold the live slot, and a player would
read that unrelated campaign's content instead of their own.

Every test here uses real ACCOUNT-mode multi-campaign state (two campaigns,
real membership) via subprocess isolation with a throwaway DATA_DIR, mirroring
tests/test_chronicle_publish.py and tests/test_chronicle_auth.py. Assertions
run entirely through the Flask test client (c.get/c.post) rather than calling
internal helpers directly, since several of those helpers resolve the active
campaign from flask.session and therefore need a real request in flight.
"""
import os
import sys
import subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    return subprocess.run(
        [sys.executable, '-c', "import os, sys\nsys.path.insert(0, os.getcwd())\n" + body],
        capture_output=True, text=True, cwd=_REPO)


# Shared setup: a site admin ("gm"), two real campaigns (A = "Shades of
# Blood", B = "Ember Court"), and the LIVE SLOT pinned to B. Everything after
# this block runs as a distinct test scenario appended to _BOOT.
_BOOT = '''
import tempfile, os, io, json, zipfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["GM_PASSWORD"] = ""
import app as A
from core import storage, auth, campaigns
c = A.app.test_client()

def zip_for(cid, text, session_number=1):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # section "recap" (with session_updated) is what /chronicle's home
        # route actually injects as the latest-recap fragment -- see
        # chronicle_home.html's {% if latest_recap %} block.
        payload = {"schema_version": 1, "session_number": session_number,
                   "pages": [{"slug": "home", "section": "recap", "title": "Session Recap",
                              "recipients": "all", "session_updated": session_number,
                              "source": "content/home.md"}]}
        if cid is not _MISSING:
            payload["campaign_id"] = cid
        z.writestr("manifest.json", json.dumps(payload))
        z.writestr("content/home.md", "# " + text)
    buf.seek(0)
    return buf.read()

_MISSING = object()

def post_zip(client, cid, text, headers=None, session_number=1):
    return client.post("/api/chronicle/publish",
                        data={"archive": (io.BytesIO(zip_for(cid, text, session_number)), "c.zip")},
                        content_type="multipart/form-data", headers=headers)

assert c.post("/setup", data={"username":"gm","password":"secret1","display_name":"GM"}).status_code == 302
assert c.post("/campaigns/new", data={"name":"Shades of Blood","system":"pf2e"}).status_code == 302
assert c.post("/campaigns/new", data={"name":"Ember Court","system":"pf2e"}).status_code == 302
names = {campaigns.get_campaign(x)["name"]: x for x in storage.list_campaign_ids()}
cid_a = names["Shades of Blood"]
cid_b = names["Ember Court"]
gm_user = auth.get_user_by_username("gm")

# Pin the LIVE SLOT to B, via the admin's own session -- admin is GM of both
# campaigns by construction (create_campaign makes the creator its GM), so
# this also sets the admin session's OWN active campaign to B.
assert c.post("/campaign/"+cid_b+"/activate").status_code == 302
assert storage.get_live_campaign_id() == cid_b
'''


def test_publish_lands_in_named_campaign_not_live_slot():
    # Publish targets A while the live slot is B (set up in _BOOT). The admin
    # session's own active campaign is also B at this point -- proving the
    # route resolves its target from the manifest, not from the live slot NOR
    # from the caller's own active campaign.
    r = _run(_BOOT + '''
r = post_zip(c, cid_a, "Content for A")
assert r.status_code == 200, r.data
j = r.get_json()
assert j["ok"] is True, j
assert j["campaign_id"] == cid_a, j
assert j["campaign_name"] == "Shades of Blood", j

# Filesystem-level proof: A's chronicle dir got the publish, B's (the live
# slot) did not.
assert os.path.isdir(os.path.join(storage.chronicle_dir(cid_a), "content"))
assert os.path.islink(os.path.join(storage.chronicle_dir(cid_a), "current"))
assert not os.path.exists(os.path.join(storage.chronicle_dir(cid_b), "current"))
print("ROUTING_OK")
''')
    assert 'ROUTING_OK' in r.stdout, r.stdout + r.stderr


def test_viewer_on_campaign_a_sees_a_not_b_while_b_is_live():
    # Publish DIFFERENT content to A and to B, then confirm a player scoped to
    # A sees only A's content through the real /chronicle route -- this is
    # the exact bug scenario: the GM published, then a browsing user landed
    # on the WRONG campaign's Chronicle.
    r = _run(_BOOT + '''
assert post_zip(c, cid_a, "Shades of Blood recap").status_code == 200
assert post_zip(c, cid_b, "Ember Court recap").status_code == 200

auth.create_user("alice", "pw_alice12", "Alice")
alice = auth.get_user_by_username("alice")
campaigns.add_member(cid_a, alice["id"], "player")

p = A.app.test_client()
assert p.post("/login", data={"username":"alice","password":"pw_alice12"}).status_code == 302
assert p.post("/campaign/"+cid_a+"/activate").status_code == 302

home = p.get("/chronicle").data
assert b"Shades of Blood recap" in home, home
assert b"Ember Court recap" not in home, home
print("ISOLATION_OK")
''')
    assert 'ISOLATION_OK' in r.stdout, r.stdout + r.stderr


def test_empty_state_renders_for_campaign_with_nothing_published():
    # Publish only to A. A member of B (never published to) must still see
    # the empty state, not A's content and not a crash.
    r = _run(_BOOT + '''
assert post_zip(c, cid_a, "Shades of Blood recap").status_code == 200

auth.create_user("bob", "pw_bob1234", "Bob")
bob = auth.get_user_by_username("bob")
campaigns.add_member(cid_b, bob["id"], "player")

p = A.app.test_client()
assert p.post("/login", data={"username":"bob","password":"pw_bob1234"}).status_code == 302
assert p.post("/campaign/"+cid_b+"/activate").status_code == 302

rv = p.get("/chronicle")
assert rv.status_code == 200, rv.status_code
assert b"opens after your first session" in rv.data, rv.data
assert b"Shades of Blood recap" not in rv.data, rv.data
print("EMPTY_STATE_OK")
''')
    assert 'EMPTY_STATE_OK' in r.stdout, r.stdout + r.stderr


def test_unknown_campaign_id_is_rejected_no_write_no_fallback():
    # A campaign_id that doesn't name any real campaign must be rejected
    # outright -- never silently redirected to the live slot (B).
    r = _run(_BOOT + '''
bogus = "0" * 32
r = post_zip(c, bogus, "Should never land anywhere")
assert r.status_code == 400, (r.status_code, r.data)
j = r.get_json()
assert j["ok"] is False and "campaign_id" in j["error"], j

# Nothing was written anywhere: not to the bogus id (can't even form a path
# for a made-up id under a real campaign dir check), and NOT to the live
# slot B either -- the precise fallback this closes.
assert not os.path.exists(os.path.join(storage.chronicle_dir(cid_b), "current"))
assert not os.path.exists(os.path.join(storage.chronicle_dir(cid_a), "current"))

# A missing campaign_id entirely is rejected the same way.
r2 = post_zip(c, _MISSING, "Also should never land anywhere")
assert r2.status_code == 400, (r2.status_code, r2.data)
assert not os.path.exists(os.path.join(storage.chronicle_dir(cid_b), "current"))
print("UNKNOWN_REJECTED_OK")
''')
    assert 'UNKNOWN_REJECTED_OK' in r.stdout, r.stdout + r.stderr


def test_gm_of_campaign_a_cannot_publish_into_campaign_b():
    # Alice is GM of A only (not a member of B at all). Her session's active
    # campaign is A. A publish whose manifest targets B must be refused, even
    # though check_gm_access's coarse gate passes her (she IS a GM -- just
    # not of B).
    r = _run(_BOOT + '''
auth.create_user("alice", "pw_alice12", "Alice")
alice = auth.get_user_by_username("alice")
campaigns.add_member(cid_a, alice["id"], "gm")

p = A.app.test_client()
assert p.post("/login", data={"username":"alice","password":"pw_alice12"}).status_code == 302
with p.session_transaction() as s:
    s["active_campaign_id"] = cid_a   # do NOT go through /activate -- that
                                       # would also flip the live slot to A,
                                       # muddying what this test isolates.

r = post_zip(p, cid_b, "Alice should not be able to write this")
assert r.status_code == 403, (r.status_code, r.data)
assert not os.path.exists(os.path.join(storage.chronicle_dir(cid_b), "current"))

# Sanity: the SAME alice CAN publish to her own campaign A.
r2 = post_zip(p, cid_a, "Alices own campaign")
assert r2.status_code == 200, (r2.status_code, r2.data)
assert r2.get_json()["campaign_id"] == cid_a
print("AUTH_SCOPED_OK")
''')
    assert 'AUTH_SCOPED_OK' in r.stdout, r.stdout + r.stderr


def test_status_and_rollback_operate_on_callers_own_campaign_not_live_slot():
    # Publish DIFFERENT content to A and B. The admin's session is active on
    # B (the live slot) by default from _BOOT; switch it to A directly (not
    # via /activate, to keep the live slot pinned at B) and confirm
    # /api/chronicle/status reports A's publish, not B's.
    r = _run(_BOOT + '''
r1 = post_zip(c, cid_a, "A v1", session_number=1)
assert r1.status_code == 200, r1.data
r2 = post_zip(c, cid_a, "A v2", session_number=2)
assert r2.status_code == 200, r2.data
assert post_zip(c, cid_b, "B v1", session_number=9).status_code == 200

with c.session_transaction() as s:
    s["active_campaign_id"] = cid_a   # caller now "acting on" A, live slot stays B

j = c.get("/api/chronicle/status").get_json()
assert j["published"] is True, j
assert j["campaign_id"] == cid_a, j
assert j["session_number"] == 2, j          # A's SECOND publish, not B's session 9
assert j["can_rollback"] is True, j         # A has two distinct publishes

rb = c.post("/api/chronicle/rollback")
assert rb.status_code == 200 and rb.get_json()["ok"], rb.data
assert rb.get_json()["campaign_id"] == cid_a

j2 = c.get("/api/chronicle/status").get_json()
assert j2["session_number"] == 1, j2        # rolled back within A only

# B is untouched by any of this.
jb_manifest = json.load(open(os.path.join(
    os.path.realpath(os.path.join(storage.chronicle_dir(cid_b), "current")), "manifest.json")))
assert jb_manifest["session_number"] == 9
print("STATUS_ROLLBACK_SCOPED_OK")
''')
    assert 'STATUS_ROLLBACK_SCOPED_OK' in r.stdout, r.stdout + r.stderr


def test_cross_campaign_asset_isolation():
    # The "classic hole": /chronicle/assets/<path> serves files by path, so a
    # viewer scoped to campaign A must never be able to pull an asset that
    # only exists in campaign B's published content -- not even by guessing
    # B's exact filename, and not even while B holds the server-wide live
    # slot (the original bug's shape: live slot != the viewer's own
    # campaign). Publish DIFFERENT content, each with its own asset file, to
    # A and to B, including a same-named asset in both to rule out a naive
    # "search by basename across campaigns" implementation too.
    r = _run(_BOOT + '''
def zip_with_assets(cid, marker, assets, session_number=1):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        payload = {"schema_version": 1, "campaign_id": cid, "session_number": session_number,
                   "pages": [{"slug": "home", "section": "recap", "title": "Session Recap",
                              "recipients": "all", "session_updated": session_number,
                              "source": "content/home.md",
                              "assets": [name for name, _ in assets]}]}
        z.writestr("manifest.json", json.dumps(payload))
        z.writestr("content/home.md", "# " + marker)
        for name, data in assets:
            z.writestr("assets/" + name, data)
    buf.seek(0)
    return buf.read()

rA = c.post("/api/chronicle/publish",
            data={"archive": (io.BytesIO(zip_with_assets(
                cid_a, "A_ONLY_MARKER_TEXT",
                [("a-image.png", b"A-ASSET-BYTES"), ("shared.png", b"A-SHARED-BYTES")])), "a.zip")},
            content_type="multipart/form-data")
assert rA.status_code == 200, rA.data
rB = c.post("/api/chronicle/publish",
            data={"archive": (io.BytesIO(zip_with_assets(
                cid_b, "B_ONLY_MARKER_TEXT",
                [("b-image.png", b"B-ASSET-BYTES"), ("shared.png", b"B-SHARED-BYTES")])), "b.zip")},
            content_type="multipart/form-data")
assert rB.status_code == 200, rB.data

auth.create_user("carol", "pw_carol12", "Carol")
carol = auth.get_user_by_username("carol")
campaigns.add_member(cid_a, carol["id"], "player")

p = A.app.test_client()
assert p.post("/login", data={"username":"carol","password":"pw_carol12"}).status_code == 302
assert p.post("/campaign/"+cid_a+"/activate").status_code == 302
# Carol is not a GM, so activating never moved the live slot -- it is still
# pinned to B from _BOOT. This is exactly the bug's shape: her own campaign
# (A) is not the server-wide live slot (B).
assert storage.get_live_campaign_id() == cid_b

own = p.get("/chronicle/assets/a-image.png")
assert own.status_code == 200, (own.status_code, own.data)
assert own.data == b"A-ASSET-BYTES", own.data

other = p.get("/chronicle/assets/b-image.png")
assert other.status_code == 404, (other.status_code, other.data)

shared = p.get("/chronicle/assets/shared.png")
assert shared.status_code == 200, (shared.status_code, shared.data)
assert shared.data == b"A-SHARED-BYTES", shared.data   # never B's bytes under the same name

home = p.get("/chronicle").data
assert b"A_ONLY_MARKER_TEXT" in home, home
assert b"B_ONLY_MARKER_TEXT" not in home, home
print("ASSET_ISOLATION_OK")
''')
    assert 'ASSET_ISOLATION_OK' in r.stdout, r.stdout + r.stderr


def test_non_gm_player_cannot_publish():
    # An authenticated but non-GM member of campaign A must be refused
    # outright by the coarse check_gm_access prefix gate -- before the route
    # ever parses the manifest -- and must leave every chronicle dir
    # (her own campaign, the live slot, and the legacy flat layout) untouched.
    r = _run(_BOOT + '''
auth.create_user("dave", "pw_dave1234", "Dave")
dave = auth.get_user_by_username("dave")
campaigns.add_member(cid_a, dave["id"], "player")

p = A.app.test_client()
assert p.post("/login", data={"username":"dave","password":"pw_dave1234"}).status_code == 302
with p.session_transaction() as s:
    s["active_campaign_id"] = cid_a   # a plain player never touches the live
                                       # slot via /activate anyway, but set it
                                       # directly to stay consistent with the
                                       # other GM-scoping tests in this file.

rr = post_zip(p, cid_a, "Dave the player should never be able to publish")
assert rr.status_code == 403, (rr.status_code, rr.data)

assert not os.path.exists(os.path.join(storage.chronicle_dir(cid_a), "current"))
assert not os.path.exists(os.path.join(storage.chronicle_dir(cid_b), "current"))
assert not os.path.exists(os.path.join(A.DATA_DIR, "chronicle", "current"))
print("PLAYER_CANNOT_PUBLISH_OK")
''')
    assert 'PLAYER_CANNOT_PUBLISH_OK' in r.stdout, r.stdout + r.stderr


def test_malformed_campaign_id_types_are_rejected():
    # manifest.json comes from inside an uploaded zip, so campaign_id is
    # attacker-influenced. Every non-string JSON type must be rejected with a
    # 4xx and must never write anywhere -- in particular must never silently
    # fall back to the live slot (cid_b), which is the exact fallback this
    # fix closed for the (already-covered) unknown-string-id case.
    r = _run(_BOOT + '''
bad_ids = [
    ("int", 424242),
    ("list", [cid_a]),
    ("dict", {"id": cid_a}),
    ("bool_true", True),
    ("bool_false", False),
    ("null", None),
]
for label, bad_cid in bad_ids:
    rr = post_zip(c, bad_cid, "Malformed campaign_id probe: " + label)
    assert 400 <= rr.status_code < 500, (label, rr.status_code, rr.data)
    j = rr.get_json()
    assert j is not None and j.get("ok") is False, (label, rr.data)
    assert not os.path.exists(os.path.join(storage.chronicle_dir(cid_a), "current")), label
    assert not os.path.exists(os.path.join(storage.chronicle_dir(cid_b), "current")), label
    assert not os.path.exists(os.path.join(A.DATA_DIR, "chronicle", "current")), label
print("MALFORMED_TYPES_REJECTED_OK")
''')
    assert 'MALFORMED_TYPES_REJECTED_OK' in r.stdout, r.stdout + r.stderr


def test_chronicle_token_does_not_unlock_other_gm_api():
    # A valid X-Chronicle-Token exists solely to let the headless PR0 build
    # tool hit /api/chronicle/publish without a real login. It must unlock
    # ONLY /api/chronicle* -- confirm it is refused on an unrelated GM-gated
    # API prefix (/api/tracker_state), even though the header carries a
    # token that is genuinely valid.
    r = _run(_BOOT + '''
os.environ["CHRONICLE_PUBLISH_TOKEN"] = "sekret-token-value-xyz"
headers = {"X-Chronicle-Token": "sekret-token-value-xyz"}
anon = A.app.test_client()   # no login/session at all -- the token is the
                              # only credential in play here

# Sanity: the token really does unlock the chronicle API it exists for.
st = anon.get("/api/chronicle/status", headers=headers)
assert st.status_code == 200, (st.status_code, st.data)

# It must NOT unlock an unrelated GM-only API prefix.
tr = anon.get("/api/tracker_state", headers=headers)
assert tr.status_code == 403, (tr.status_code, tr.data)

# The wrong token is refused even on the chronicle route -- confirms the 200
# above really came from the token match, not some other bypass.
bad = anon.get("/api/chronicle/status", headers={"X-Chronicle-Token": "not-the-token"})
assert bad.status_code == 403, (bad.status_code, bad.data)
print("TOKEN_CONFINEMENT_OK")
''')
    assert 'TOKEN_CONFINEMENT_OK' in r.stdout, r.stdout + r.stderr


def test_legacy_mode_without_accounts_still_publishes_to_flat_layout():
    # No accounts at all (true legacy/pre-migration): campaign_id is not
    # applicable (there is no campaigns table to validate it against), so the
    # publish must keep working exactly as before this fix, writing to the
    # single flat DATA_DIR/chronicle layout.
    r = _run('''
import tempfile, os, io, json, zipfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(); os.environ["GM_PASSWORD"] = ""
import app as A
c = A.app.test_client()
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("manifest.json", json.dumps({
        "schema_version": 1, "campaign_id": "not-a-real-campaign", "session_number": 1,
        "pages": [{"slug": "home", "section": "home", "title": "Home",
                   "recipients": "all", "source": "content/home.md"}]}))
    z.writestr("content/home.md", "# Legacy table")
buf.seek(0)
r = c.post("/api/chronicle/publish", data={"archive": (buf, "c.zip")},
           content_type="multipart/form-data")
assert r.status_code == 200, (r.status_code, r.data)
j = r.get_json()
assert j["campaign_id"] is None, j          # no campaigns table -> nothing to scope to
content = os.path.join(A.DATA_DIR, "chronicle", "current")
assert os.path.islink(content)
print("LEGACY_OK")
''')
    assert 'LEGACY_OK' in r.stdout, r.stdout + r.stderr


# --------------------------------------------------------------------------
# /api/chronicle/unpublish (2026-07-29 addition)
#
# Retracts a campaign's published Chronicle: removes `current`/`previous`
# AND the underlying rendered content -- for a spoiler accidentally
# published, or an orphaned cross-campaign copy left by the routing bug the
# tests above cover. DESTRUCTIVE, so every test here proves both that the
# right thing gets deleted AND that everything else survives untouched.
# --------------------------------------------------------------------------

def test_unpublish_removes_target_content_leaves_other_campaign_intact():
    r = _run(_BOOT + '''
assert post_zip(c, cid_a, "A recap to retract").status_code == 200
assert post_zip(c, cid_b, "B recap must survive").status_code == 200

a_root = storage.chronicle_dir(cid_a)
b_root = storage.chronicle_dir(cid_b)
a_current_target = os.path.realpath(os.path.join(a_root, "current"))
b_current_target = os.path.realpath(os.path.join(b_root, "current"))
assert os.path.isdir(a_current_target) and os.path.isdir(b_current_target)
b_manifest_before = open(os.path.join(b_current_target, "manifest.json")).read()

r = c.post("/api/chronicle/unpublish", json={"campaign_id": cid_a})
assert r.status_code == 200, r.data
j = r.get_json()
assert j["ok"] is True, j
assert j["campaign_id"] == cid_a, j
assert j["campaign_name"] == "Shades of Blood", j
assert j["removed"] is True, j

# A: pointers AND the underlying rendered bytes are gone -- not just
# unlinked. This is the "actually retracts the spoiler" requirement.
assert not os.path.islink(os.path.join(a_root, "current"))
assert not os.path.exists(os.path.join(a_root, "current"))
assert not os.path.isdir(os.path.join(a_root, "content"))
assert not os.path.isdir(a_current_target), \\
    "unpublish must delete the rendered content, not just repoint/remove the pointer"

# A's players now get the empty state through the real read path. The
# admin session's own active campaign is B (the live slot, from _BOOT), so
# flip it to A directly (not via /activate, which would also move the live
# slot) purely to view A's Chronicle as A's own reader would.
with c.session_transaction() as s:
    s["active_campaign_id"] = cid_a
rv = c.get("/chronicle")
assert b"opens after your first session" in rv.data, rv.data
with c.session_transaction() as s:
    s["active_campaign_id"] = cid_b   # restore, so the B assertions below
                                       # reflect the live-slot session again

# B: completely untouched -- same target dir, same bytes, still live.
assert os.path.realpath(os.path.join(b_root, "current")) == b_current_target
assert os.path.isdir(b_current_target)
assert open(os.path.join(b_current_target, "manifest.json")).read() == b_manifest_before
print("UNPUBLISH_REMOVES_TARGET_OK")
''')
    assert 'UNPUBLISH_REMOVES_TARGET_OK' in r.stdout, r.stdout + r.stderr


def test_gm_of_campaign_a_cannot_unpublish_campaign_b():
    # Alice is GM of A only (not a member of B at all), same shape as the
    # equivalent publish-scoping test above.
    r = _run(_BOOT + '''
auth.create_user("alice", "pw_alice12", "Alice")
alice = auth.get_user_by_username("alice")
campaigns.add_member(cid_a, alice["id"], "gm")

assert post_zip(c, cid_b, "B content Alice must not be able to retract").status_code == 200
b_root = storage.chronicle_dir(cid_b)
b_current_target = os.path.realpath(os.path.join(b_root, "current"))

p = A.app.test_client()
assert p.post("/login", data={"username":"alice","password":"pw_alice12"}).status_code == 302
with p.session_transaction() as s:
    s["active_campaign_id"] = cid_a   # do NOT go through /activate -- keep the
                                       # live slot pinned at B from _BOOT.

r = p.post("/api/chronicle/unpublish", json={"campaign_id": cid_b})
assert r.status_code == 403, (r.status_code, r.data)
assert r.get_json()["ok"] is False, r.data
assert os.path.isdir(b_current_target), "B must remain published"
assert os.path.realpath(os.path.join(b_root, "current")) == b_current_target

# Sanity: the SAME alice CAN unpublish her own campaign A (nothing published
# there yet, so this also exercises the no-op path) -- proves the 403 above
# was scoped to B specifically, not a blanket auth failure for Alice.
r2 = p.post("/api/chronicle/unpublish", json={"campaign_id": cid_a})
assert r2.status_code == 200, (r2.status_code, r2.data)
assert r2.get_json()["ok"] is True
print("GM_SCOPE_UNPUBLISH_OK")
''')
    assert 'GM_SCOPE_UNPUBLISH_OK' in r.stdout, r.stdout + r.stderr


def test_non_gm_player_cannot_unpublish():
    r = _run(_BOOT + '''
auth.create_user("dave", "pw_dave1234", "Dave")
dave = auth.get_user_by_username("dave")
campaigns.add_member(cid_a, dave["id"], "player")

assert post_zip(c, cid_a, "Dave must not be able to retract this").status_code == 200
a_root = storage.chronicle_dir(cid_a)
a_current_target = os.path.realpath(os.path.join(a_root, "current"))

p = A.app.test_client()
assert p.post("/login", data={"username":"dave","password":"pw_dave1234"}).status_code == 302
with p.session_transaction() as s:
    s["active_campaign_id"] = cid_a

r = p.post("/api/chronicle/unpublish", json={"campaign_id": cid_a})
assert r.status_code == 403, (r.status_code, r.data)
assert os.path.isdir(a_current_target), \\
    "a non-GM player must never be able to retract the Chronicle"
assert os.path.realpath(os.path.join(a_root, "current")) == a_current_target
print("NON_GM_CANNOT_UNPUBLISH_OK")
''')
    assert 'NON_GM_CANNOT_UNPUBLISH_OK' in r.stdout, r.stdout + r.stderr


def test_missing_unknown_malformed_campaign_id_rejected_nothing_removed():
    # campaign_id must be given explicitly and must name a real campaign --
    # this route must NEVER infer a target from the live slot (B, pinned by
    # _BOOT) or the caller's own active campaign.
    r = _run(_BOOT + '''
assert post_zip(c, cid_a, "A must survive every rejected unpublish attempt").status_code == 200
assert post_zip(c, cid_b, "B must survive every rejected unpublish attempt").status_code == 200
a_target = os.path.realpath(os.path.join(storage.chronicle_dir(cid_a), "current"))
b_target = os.path.realpath(os.path.join(storage.chronicle_dir(cid_b), "current"))

def assert_untouched():
    assert os.path.isdir(a_target)
    assert os.path.isdir(b_target)
    assert os.path.realpath(os.path.join(storage.chronicle_dir(cid_a), "current")) == a_target
    assert os.path.realpath(os.path.join(storage.chronicle_dir(cid_b), "current")) == b_target

# Missing entirely (no campaign_id key in the body at all).
r0 = c.post("/api/chronicle/unpublish", json={})
assert 400 <= r0.status_code < 500, (r0.status_code, r0.data)
assert r0.get_json()["ok"] is False, r0.data
assert_untouched()

# Well-formed id, but names no real campaign.
bogus = "0" * 32
r1 = c.post("/api/chronicle/unpublish", json={"campaign_id": bogus})
assert 400 <= r1.status_code < 500, (r1.status_code, r1.data)
assert r1.get_json()["ok"] is False, r1.data
assert_untouched()

# Malformed JSON types (campaign_id comes from a request body, so every
# non-string type must be rejected the same way the publish manifest's
# campaign_id is).
bad_ids = [424242, [cid_a], {"id": cid_a}, True, False, None, ""]
for bad_cid in bad_ids:
    rr = c.post("/api/chronicle/unpublish", json={"campaign_id": bad_cid})
    assert 400 <= rr.status_code < 500, (bad_cid, rr.status_code, rr.data)
    assert rr.get_json()["ok"] is False, (bad_cid, rr.data)
    assert_untouched()
print("MISSING_UNKNOWN_MALFORMED_REJECTED_OK")
''')
    assert 'MISSING_UNKNOWN_MALFORMED_REJECTED_OK' in r.stdout, r.stdout + r.stderr


def test_unpublish_noop_when_nothing_published():
    r = _run(_BOOT + '''
assert c.post("/campaigns/new", data={"name":"Empty Table","system":"pf2e"}).status_code == 302
names2 = {campaigns.get_campaign(x)["name"]: x for x in storage.list_campaign_ids()}
cid_c = names2["Empty Table"]
assert not os.path.exists(os.path.join(storage.chronicle_dir(cid_c), "current"))

r = c.post("/api/chronicle/unpublish", json={"campaign_id": cid_c})
assert r.status_code == 200, (r.status_code, r.data)
j = r.get_json()
assert j["ok"] is True, j
assert j["removed"] is False, j              # nothing was there -> no-op, not an error
assert j["campaign_id"] == cid_c, j
assert j["campaign_name"] == "Empty Table", j

# Calling it again (double-unpublish) stays a clean no-op.
r2 = c.post("/api/chronicle/unpublish", json={"campaign_id": cid_c})
assert r2.status_code == 200, (r2.status_code, r2.data)
j2 = r2.get_json()
assert j2["ok"] is True and j2["removed"] is False, j2
print("NOOP_UNPUBLISH_OK")
''')
    assert 'NOOP_UNPUBLISH_OK' in r.stdout, r.stdout + r.stderr


def test_blast_radius_confined_to_chronicle_root():
    # Proves unpublish only ever touches the target campaign's chronicle
    # root: its own campaign directory (and non-chronicle data in it), the
    # chronicle root itself (just emptied, not removed), and every sibling
    # campaign's directory/data must all survive byte-for-byte.
    r = _run(_BOOT + '''
assert post_zip(c, cid_a, "A content to retract").status_code == 200
assert post_zip(c, cid_b, "B content that must survive").status_code == 200

a_camp_dir = storage.campaign_dir(cid_a)
b_camp_dir = storage.campaign_dir(cid_b)

# Plant non-chronicle campaign data for A that must survive an unpublish.
marker_dir = os.path.join(a_camp_dir, "party_data")
os.makedirs(marker_dir, exist_ok=True)
marker_file = os.path.join(marker_dir, "marker.json")
with open(marker_file, "w") as fh:
    json.dump({"marker": "must survive"}, fh)

r = c.post("/api/chronicle/unpublish", json={"campaign_id": cid_a})
assert r.status_code == 200 and r.get_json()["ok"] is True, r.data

# A's own campaign directory, campaign.json, and unrelated data all survive
# -- only the chronicle subtree was touched.
assert os.path.isdir(a_camp_dir)
assert campaigns.get_campaign(cid_a)["name"] == "Shades of Blood"
assert os.path.isfile(marker_file)
assert json.load(open(marker_file))["marker"] == "must survive"

# The chronicle root ITSELF still exists (only current/previous/content
# inside it were removed) -- unpublish is not campaign deletion.
assert os.path.isdir(storage.chronicle_dir(cid_a))

# Sibling campaign B: directory, campaign.json, and published chronicle all
# completely untouched.
assert os.path.isdir(b_camp_dir)
assert campaigns.get_campaign(cid_b)["name"] == "Ember Court"
assert os.path.isdir(os.path.realpath(os.path.join(storage.chronicle_dir(cid_b), "current")))
print("BLAST_RADIUS_CONFINED_OK")
''')
    assert 'BLAST_RADIUS_CONFINED_OK' in r.stdout, r.stdout + r.stderr
