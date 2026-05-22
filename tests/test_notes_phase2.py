"""Phase 2 editor support: live-preview render, title autocomplete, attachments.

Same hermetic setup as test_notes_crud.py — the vault root is redirected to a
tmp dir via monkeypatching ``notes._VAULT_DATA_DIR``.
"""

from __future__ import annotations

import io
import pytest


@pytest.fixture
def notes(monkeypatch, tmp_path):
    from services import notes as notes_mod
    monkeypatch.setattr(notes_mod, "_VAULT_DATA_DIR", tmp_path)
    notes_mod.invalidate_tree_cache()
    notes_mod.invalidate_index()
    return notes_mod


@pytest.fixture
def client(monkeypatch, tmp_path):
    from services import notes as notes_mod
    monkeypatch.setattr(notes_mod, "_VAULT_DATA_DIR", tmp_path)
    notes_mod.invalidate_tree_cache()
    notes_mod.invalidate_index()
    import app
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["gm_authenticated"] = True
    return c


# ───────────────────────── render_preview ─────────────────────────
def test_render_preview_strips_frontmatter_and_renders_markdown(notes):
    html = notes.render_preview("---\ntype: npc\n---\n# Heading\n\nSome **bold** text.")
    assert "<h1" in html and "Heading" in html
    assert "<strong>bold</strong>" in html
    assert "type: npc" not in html      # frontmatter not rendered
    assert "---" not in html


def test_render_preview_resolves_wikilinks_and_callouts(notes):
    notes.create_note("Otari.md", body="town")
    html = notes.render_preview("Link to [[Otari]] and [[Ghost]].")
    assert 'class="wikilink"' in html            # Otari resolves
    assert 'wikilink-broken' in html             # Ghost does not
    callout = notes.render_preview("> [!note] Heads up\n> body here")
    assert 'class="cal cal-note"' in callout


def test_render_preview_handles_empty(notes):
    assert notes.render_preview("") == ""
    assert notes.render_preview(None) == ""


# ───────────────────────── list_titles ─────────────────────────
def test_list_titles_returns_sorted_title_path(notes):
    notes.create_note("NPCs/Romi.md", body="a")
    notes.create_note("Otari.md", body="b")
    titles = notes.list_titles()
    by_title = {t["title"]: t["path"] for t in titles}
    assert by_title.get("Romi") == "NPCs/Romi.md"
    assert by_title.get("Otari") == "Otari.md"
    names = [t["title"].lower() for t in titles]
    assert names == sorted(names)


# ───────────────────────── save_attachment ─────────────────────────
def test_save_attachment_lands_in_attachments_and_dedupes(notes, tmp_path):
    rel1 = notes.save_attachment("map.png", b"\x89PNG\r\n")
    assert rel1 == "zz_Attachments/map.png"
    assert (tmp_path / "zz_Attachments" / "map.png").is_file()
    rel2 = notes.save_attachment("map.png", b"second")
    assert rel2 == "zz_Attachments/map-1.png"        # de-duped
    assert (tmp_path / "zz_Attachments" / "map-1.png").is_file()


def test_save_attachment_sanitizes_name(notes, tmp_path):
    rel = notes.save_attachment("../../evil name.png", b"x")
    assert rel.startswith("zz_Attachments/")
    assert ".." not in rel


# ───────────────────────── endpoints ─────────────────────────
def test_endpoint_preview(client):
    r = client.post("/api/notes/preview", json={"body": "# Hi\n\ntext **b**"})
    assert r.status_code == 200
    j = r.get_json()
    assert j["success"] and "<h1" in j["html"] and "<strong>b</strong>" in j["html"]


def test_endpoint_titles(client, tmp_path):
    client.post("/api/notes/create", json={"title": "Sandpoint", "template": "blank"})
    r = client.get("/api/notes/titles")
    assert r.status_code == 200
    titles = [t["title"] for t in r.get_json()["titles"]]
    assert "Sandpoint" in titles


def test_endpoint_attachment_upload(client, tmp_path):
    data = {"file": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "token.png")}
    r = client.post("/api/notes/attachment", data=data, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["path"] == "zz_Attachments/token.png"
    assert (tmp_path / "zz_Attachments" / "token.png").is_file()


def test_endpoint_attachment_requires_file(client):
    r = client.post("/api/notes/attachment", data={}, content_type="multipart/form-data")
    assert r.status_code == 400
