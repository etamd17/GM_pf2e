"""Phase 3 (transclusion + ```query) and Phase 4 (connection graph) tests.

Hermetic: the vault root is redirected to a tmp dir (monkeypatch
``notes._VAULT_DATA_DIR``); all caches are cleared per test.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def notes(monkeypatch, tmp_path):
    from services import notes as notes_mod
    monkeypatch.setattr(notes_mod, "_VAULT_DATA_DIR", tmp_path)
    notes_mod.invalidate_tree_cache()
    notes_mod.invalidate_index()
    notes_mod._RENDER_CACHE.clear()
    return notes_mod


@pytest.fixture
def client(monkeypatch, tmp_path):
    from services import notes as notes_mod
    monkeypatch.setattr(notes_mod, "_VAULT_DATA_DIR", tmp_path)
    notes_mod.invalidate_tree_cache()
    notes_mod.invalidate_index()
    notes_mod._RENDER_CACHE.clear()
    import app
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["gm_authenticated"] = True
    return c


# ───────────────────────── transclusion ─────────────────────────
def test_transclusion_inlines_target(notes):
    notes.create_note("A.md", body="Alpha content here.")
    notes.create_note("B.md", body="Intro.\n\n![[A]]\n\nOutro.")
    html = notes.render("B.md").html
    assert "Alpha content here." in html      # target body inlined
    assert "note-transclude" in html          # wrapped
    assert "Intro." in html and "Outro." in html


def test_transclusion_strips_target_frontmatter(notes):
    notes.create_note("A.md", body="---\ntype: npc\n---\nVisible body.")
    notes.create_note("B.md", body="![[A]]")
    html = notes.render("B.md").html
    assert "Visible body." in html
    assert "type: npc" not in html


def test_transclusion_broken_link(notes):
    notes.create_note("B.md", body="![[Ghost]]")
    html = notes.render("B.md").html
    assert "wikilink-broken" in html


def test_transclusion_cycle_terminates(notes):
    notes.create_note("X.md", body="X embeds ![[Y]]")
    notes.create_note("Y.md", body="Y embeds ![[X]]")
    html = notes.render("X.md").html      # must not infinite-loop
    assert "note-transclude" in html
    assert "embed not expanded" in html   # the cycle was cut


def test_image_embed_still_renders_as_img(notes):
    notes.create_note("B.md", body="![[map.png]]")
    html = notes.render("B.md").html
    # image embeds go through the asset path, not transclusion
    assert "note-transclude" not in html


# ───────────────────────── query block / query_notes ─────────────────────────
def test_query_notes_filters_by_frontmatter_and_folder(notes):
    notes.create_note("NPCs/Romi.md", body="---\ntype: npc\nstatus: active\n---\nr")
    notes.create_note("NPCs/Vael.md", body="---\ntype: npc\nstatus: dead\n---\nv")
    notes.create_note("Places/Otari.md", body="---\ntype: location\n---\no")
    npcs = {r["path"] for r in notes.query_notes({"type": "npc"})}
    assert npcs == {"NPCs/Romi.md", "NPCs/Vael.md"}
    active = {r["path"] for r in notes.query_notes({"type": "npc", "status": "active"})}
    assert active == {"NPCs/Romi.md"}
    in_places = {r["path"] for r in notes.query_notes({"folder": "Places"})}
    assert in_places == {"Places/Otari.md"}


def test_query_notes_by_tag(notes):
    notes.create_note("A.md", body="has a #faction tag")
    notes.create_note("B.md", body="---\ntags: [faction, lore]\n---\nb")
    notes.create_note("C.md", body="nothing")
    tagged = {r["path"] for r in notes.query_notes({"tag": "faction"})}
    assert tagged == {"A.md", "B.md"}


def test_query_block_renders_list(notes):
    notes.create_note("NPCs/Romi.md", body="---\ntype: npc\n---\nr")
    notes.create_note("Index.md", body="Roster:\n\n```query\ntype: npc\n```\n")
    html = notes.render("Index.md").html
    assert "note-query" in html
    assert "Romi" in html
    assert 'href="/gm/notes/view/NPCs/Romi.md"' in html


# ───────────────────────── graph / neighbors ─────────────────────────
def test_neighbors_includes_in_and_out_links(notes):
    notes.create_note("A.md", body="links [[B]]")
    notes.create_note("B.md", body="b")
    notes.create_note("C.md", body="links [[A]]")
    g = notes.neighbors("A.md")
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {"A.md", "B.md", "C.md"}
    edges = {(e["source"], e["target"]) for e in g["edges"]}
    assert ("A.md", "B.md") in edges       # outbound
    assert ("C.md", "A.md") in edges       # inbound
    assert g["center"] == "A.md"


def test_neighbors_node_carries_type(notes):
    notes.create_note("A.md", body="---\ntype: npc\n---\nlinks [[B]]")
    notes.create_note("B.md", body="b")
    g = notes.neighbors("A.md")
    a = next(n for n in g["nodes"] if n["id"] == "A.md")
    assert a["type"] == "npc"


def test_graph_whole_vault(notes):
    notes.create_note("A.md", body="[[B]]")
    notes.create_note("B.md", body="b")
    g = notes.graph()
    ids = {n["id"] for n in g["nodes"]}
    assert {"A.md", "B.md"} <= ids
    assert {"source": "A.md", "target": "B.md"} in g["edges"]


# ───────────────────────── endpoints ─────────────────────────
def test_endpoint_neighbors(client):
    client.post("/api/notes/create", json={"title": "A", "template": "blank"})
    client.post("/api/notes/create", json={"title": "B", "template": "blank"})
    from services import notes as notes_mod
    notes_mod.save("A.md", "see [[B]]")
    r = client.get("/api/notes/neighbors?path=A.md")
    assert r.status_code == 200, r.get_json()
    ids = {n["id"] for n in r.get_json()["nodes"]}
    assert {"A.md", "B.md"} <= ids


def test_endpoint_graph(client):
    client.post("/api/notes/create", json={"title": "Solo", "template": "blank"})
    r = client.get("/api/notes/graph")
    assert r.status_code == 200
    assert any(n["id"] == "Solo.md" for n in r.get_json()["nodes"])


def test_endpoint_neighbors_requires_path(client):
    r = client.get("/api/notes/neighbors")
    assert r.status_code == 400
