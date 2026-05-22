"""Phase 1 notes-editor CRUD: snapshot + create + delete (service fns + endpoints).

These are the first *write* operations on the vault from the website (the vault
is now the source of truth — git sync was removed). The vault root is redirected
to a tmp dir by monkeypatching ``notes._VAULT_DATA_DIR`` (``get_vault_root``
resolves it fresh on every call), so the tests run hermetically and never touch
the real vault.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def notes(monkeypatch, tmp_path):
    from services import notes as notes_mod
    monkeypatch.setattr(notes_mod, "_VAULT_DATA_DIR", tmp_path)
    notes_mod.invalidate_tree_cache()
    notes_mod.invalidate_index()
    return notes_mod


def _flatten_tree(nodes):
    out = []
    for n in nodes:
        out.append(n["path"])
        out.extend(_flatten_tree(n.get("children", [])))
    return out


# ─────────────────────────── templates ───────────────────────────
def test_templates_list_and_fill(notes):
    keys = {t["key"] for t in notes.list_templates()}
    assert {"blank", "session", "npc", "beat"} <= keys
    body = notes.template_body("npc", "Romi")
    assert "type: npc" in body and "# Romi" in body
    assert "{title}" not in body and "{date}" not in body


def test_template_unknown_key_falls_back_to_blank(notes):
    assert notes.template_body("does-not-exist", "X") == ""


# ─────────────────────────── create ───────────────────────────
def test_create_note_writes_with_template(notes, tmp_path):
    r = notes.create_note("NPCs/Romi.md", body=notes.template_body("npc", "Romi"))
    assert (tmp_path / "NPCs" / "Romi.md").is_file()
    assert r.frontmatter.get("type") == "npc"
    assert r.rel_path == "NPCs/Romi.md"


def test_create_note_appends_md_and_rejects_duplicate(notes):
    notes.create_note("Beat", body="x")  # .md appended
    assert notes.render("Beat.md").rel_path == "Beat.md"
    with pytest.raises(FileExistsError):
        notes.create_note("Beat.md", body="y")


def test_create_note_blocks_traversal(notes):
    with pytest.raises(notes.NotePathError):
        notes.create_note("../escape.md", body="x")


# ─────────────────────────── delete + snapshot ───────────────────────────
def test_delete_note_snapshots_then_removes(notes, tmp_path):
    notes.create_note("Doomed.md", body="bye")
    snap = notes.delete_note("Doomed.md")
    assert not (tmp_path / "Doomed.md").exists()
    assert snap is not None
    copies = list((tmp_path / ".snapshots").rglob("Doomed.md"))
    assert copies and copies[0].read_text(encoding="utf-8") == "bye"


def test_delete_missing_raises(notes):
    with pytest.raises(FileNotFoundError):
        notes.delete_note("nope.md")


def test_snapshots_hidden_from_tree(notes):
    notes.create_note("A.md", body="a")
    notes.delete_note("A.md")  # creates a .snapshots/ entry
    paths = _flatten_tree(notes.tree())
    assert not any(p.startswith(".snapshots") for p in paths)


# ─────────────────────────── endpoints (GM-gated) ───────────────────────────
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


def test_endpoint_create_then_delete(client, tmp_path):
    resp = client.post("/api/notes/create", json={"folder": "NPCs", "title": "Romi", "template": "npc"})
    assert resp.status_code == 200, resp.get_json()
    path = resp.get_json()["path"]
    assert (tmp_path / "NPCs" / "Romi.md").is_file()

    # duplicate → 409
    dup = client.post("/api/notes/create", json={"folder": "NPCs", "title": "Romi", "template": "npc"})
    assert dup.status_code == 409

    # delete (snapshots first) → file gone, snapshot recorded
    d = client.post("/api/notes/delete", json={"path": path})
    assert d.status_code == 200 and d.get_json()["success"]
    assert d.get_json()["snapshot"]
    assert not (tmp_path / "NPCs" / "Romi.md").exists()


def test_endpoint_create_requires_title(client):
    resp = client.post("/api/notes/create", json={"folder": "", "title": ""})
    assert resp.status_code == 400


def test_endpoint_templates_lists_seeded(client):
    resp = client.get("/api/notes/templates")
    assert resp.status_code == 200
    keys = {t["key"] for t in resp.get_json()["templates"]}
    assert {"session", "npc", "beat"} <= keys


# ─────────────────────────── rename + backlink rewrite ───────────────────────────
def _read(tmp_path, rel):
    return (tmp_path / rel).read_text(encoding="utf-8")


def test_rename_rewrites_bare_link(notes, tmp_path):
    notes.create_note("A.md", body="I am A.")
    notes.create_note("Ref.md", body="See [[A]] for details.")
    out = notes.rename_note("A.md", "C.md")
    assert out["to"] == "C.md" and out["rewritten"] == 1
    assert not (tmp_path / "A.md").exists()
    assert (tmp_path / "C.md").is_file()
    assert "[[C]]" in _read(tmp_path, "Ref.md")
    assert "[[A]]" not in _read(tmp_path, "Ref.md")


def test_rename_preserves_alias_heading_embed(notes, tmp_path):
    notes.create_note("A.md", body="a")
    notes.create_note(
        "Ref.md",
        body="alias [[A|the smith]], heading [[A#Background]], embed ![[A]]",
    )
    notes.rename_note("A.md", "Roma.md")
    txt = _read(tmp_path, "Ref.md")
    assert "[[Roma|the smith]]" in txt
    assert "[[Roma#Background]]" in txt
    assert "![[Roma]]" in txt


def test_rename_rewrites_path_style_link(notes, tmp_path):
    notes.create_note("Folder/A.md", body="a")
    notes.create_note("Ref.md", body="path link [[Folder/A]] here")
    notes.rename_note("Folder/A.md", "Folder/D.md")
    assert "[[Folder/D]]" in _read(tmp_path, "Ref.md")


def test_move_folder_updates_path_link_not_title_link(notes, tmp_path):
    notes.create_note("NPCs/A.md", body="a")
    notes.create_note("Ref.md", body="title [[A]] and path [[NPCs/A]]")
    out = notes.rename_note("NPCs/A.md", "Allies/A.md")
    txt = _read(tmp_path, "Ref.md")
    assert "[[A]]" in txt              # title unchanged on a same-name move
    assert "[[Allies/A]]" in txt       # path link follows the move
    assert out["rewritten"] == 1       # only the path link actually changed


def test_rename_skips_ambiguous_bare_title(notes, tmp_path):
    # Two notes share the title "A" → a bare [[A]] is ambiguous and must NOT
    # be hijacked when one of them is renamed.
    notes.create_note("A.md", body="top")
    notes.create_note("sub/A.md", body="nested")
    notes.create_note("Ref.md", body="ambiguous [[A]]")
    notes.rename_note("A.md", "Q.md")
    assert (tmp_path / "Q.md").is_file()
    assert "[[A]]" in _read(tmp_path, "Ref.md")   # left for manual review
    assert "[[Q]]" not in _read(tmp_path, "Ref.md")


def test_rename_snapshots_referrers(notes, tmp_path):
    notes.create_note("A.md", body="a")
    notes.create_note("Ref.md", body="[[A]]")
    out = notes.rename_note("A.md", "C.md")
    assert out["snapshot"]
    # the snapshot captured the referrer in its pre-rewrite state
    snaps = list((tmp_path / ".snapshots").rglob("Ref.md"))
    assert snaps and "[[A]]" in snaps[0].read_text(encoding="utf-8")


def test_rename_dest_exists_raises(notes):
    notes.create_note("A.md", body="a")
    notes.create_note("B.md", body="b")
    with pytest.raises(FileExistsError):
        notes.rename_note("A.md", "B.md")


def test_rename_missing_source_raises(notes):
    with pytest.raises(FileNotFoundError):
        notes.rename_note("ghost.md", "x.md")


def test_endpoint_rename(client, tmp_path):
    client.post("/api/notes/create", json={"title": "A", "template": "blank"})
    client.post("/api/notes/create", json={"title": "Ref", "template": "blank"})
    # put a link in Ref via save
    from services import notes as notes_mod
    notes_mod.save("Ref.md", "link [[A]]")
    resp = client.post("/api/notes/rename", json={"from": "A.md", "to": "C.md"})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["rewritten"] == 1
    assert "[[C]]" in _read(tmp_path, "Ref.md")


# ─────────────────────────── folder ops ───────────────────────────
def test_create_folder_and_duplicate(notes, tmp_path):
    rel = notes.create_folder("Regions")
    assert rel == "Regions" and (tmp_path / "Regions").is_dir()
    with pytest.raises(FileExistsError):
        notes.create_folder("Regions")


def test_delete_folder_snapshots_and_removes(notes, tmp_path):
    notes.create_note("Towns/Otari.md", body="o")
    notes.create_note("Towns/Sandpoint.md", body="s")
    out = notes.delete_folder("Towns")
    assert out["deleted"] == 2 and out["snapshot"]
    assert not (tmp_path / "Towns").exists()
    assert list((tmp_path / ".snapshots").rglob("Otari.md"))


def test_rename_folder_rewrites_path_links_not_title_links(notes, tmp_path):
    notes.create_note("NPCs/Romi.md", body="r")
    notes.create_note("Ref.md", body="path [[NPCs/Romi]] and title [[Romi]]")
    out = notes.rename_folder("NPCs", "Allies")
    assert out["to"] == "Allies" and out["moved"] == 1
    assert (tmp_path / "Allies" / "Romi.md").is_file()
    txt = _read(tmp_path, "Ref.md")
    assert "[[Allies/Romi]]" in txt   # path link follows the folder
    assert "[[Romi]]" in txt          # title link unchanged by a move


def test_rename_folder_updates_internal_referrer(notes, tmp_path):
    notes.create_note("NPCs/Romi.md", body="r")
    notes.create_note("NPCs/Index.md", body="see [[NPCs/Romi]]")
    notes.rename_folder("NPCs", "Allies")
    assert (tmp_path / "Allies" / "Index.md").is_file()
    assert "[[Allies/Romi]]" in _read(tmp_path, "Allies/Index.md")


def test_endpoint_folder_create_rename_delete(client, tmp_path):
    r = client.post("/api/notes/folder", json={"action": "create", "path": "Places"})
    assert r.status_code == 200 and (tmp_path / "Places").is_dir()
    client.post("/api/notes/create", json={"folder": "Places", "title": "Inn", "template": "blank"})
    rr = client.post("/api/notes/folder", json={"action": "rename", "path": "Places", "to": "Locations"})
    assert rr.status_code == 200, rr.get_json()
    assert (tmp_path / "Locations" / "Inn.md").is_file()
    rd = client.post("/api/notes/folder", json={"action": "delete", "path": "Locations"})
    assert rd.status_code == 200 and rd.get_json()["deleted"] == 1
    assert not (tmp_path / "Locations").exists()
