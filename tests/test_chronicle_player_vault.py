import logging
import os
import pathlib
import tempfile

import pytest

from tools import chronicle_build as cb

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "player_vault_sample"

# A SEPARATE fixture tree (not player_vault_sample/) carrying a planted
# [!danger] marker, used ONLY by the leak-abort tests below. Kept separate
# so player_vault_sample stays provably leak-free forever -- its own tests
# assert a clean `leaks == []` build, and a stray planted marker living in
# that tree would be one careless future edit away from silently breaking
# that guarantee (or, worse, being "fixed" by someone who doesn't realize
# it's load-bearing). A tmp_path copy-then-mutate was the other option
# considered; a static fixture tree was chosen instead because it's reused
# by more than one test below (the leak-abort test AND the
# never-touches-out_dir test) without repeating setup/copy code in each,
# and it reads the same way every other fixture-backed test in this module
# already does (FIXTURE at module scope).
LEAK_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "player_vault_leak_sample"


def _note(path, **fm):
    return {"frontmatter": fm, "body": "", "path": path}


def test_section_folder_precedence():
    assert cb._player_vault_section(_note("v/01 - Chronicle/S01.md", type="recap")) == "recap"
    assert cb._player_vault_section(_note("v/02 - Cast/Romi.md", type="npc")) == "cast"
    assert cb._player_vault_section(_note("v/06 - Party/Amadeus.md", type="pc")) == "cast"
    assert cb._player_vault_section(_note("v/04 - Atlas/Town.md", type="place")) == "atlas"
    assert cb._player_vault_section(_note("v/05 - Handouts/Letter.md", type="handout")) == "handout"
    assert cb._player_vault_section(_note("v/03 - Quests/Q.md", type="quest")) == "lore"


def test_section_type_fallback_and_home_and_default():
    assert cb._player_vault_section(_note("v/Home.md", type="dashboard")) == "home"
    assert cb._player_vault_section(_note("v/Cast/Bob.md", type="npc")) == "cast"
    assert cb._player_vault_section(_note("v/whatever.md")) == "lore"


def test_home_special_case_is_top_level_only():
    # A top-level Home.md (exactly one path segment above it - the vault
    # root itself) is the "home" special case.
    assert cb._player_vault_section(_note("v/Home.md", type="dashboard")) == "home"
    # A nested Home.md (any subfolder) must NOT resolve to "home" - it
    # falls through to whatever folder/type mapping applies, else "lore".
    assert cb._player_vault_section(_note("v/08 - Misc/Home.md")) == "lore"
    # A nested Home.md inside a MAPPED folder still resolves to that
    # folder's section, proving folder precedence beats the home case.
    assert cb._player_vault_section(_note("v/02 - Cast/Home.md")) == "cast"


def test_home_special_case_is_vault_relative_when_vault_dir_given():
    # Regression guard: the real note loader (`_load_notes`) builds
    # ABSOLUTE paths via os.path.join(root, fn), so a real vault's
    # top-level Home.md has far more than two raw path segments (e.g.
    # "/Users/gm/Documents/Campaign Chronicle/Home.md"). "Top-level" is
    # only meaningful relative to the vault root, so once `vault_dir` is
    # given, top-level-ness must be decided on the note's VAULT-RELATIVE
    # path, not a raw segment count.
    vault = "/Users/gm/Documents/Campaign Chronicle"

    # An absolute-path top-level Home.md WITH vault_dir passed maps to
    # "home" - this is the bug: without the vault-relative fix, this note's
    # absolute path has 6 segments, so the old `len(segments) == 2` check
    # would miss it and fall through to "lore".
    assert cb._player_vault_section(
        _note(vault + "/Home.md", type="dashboard"), vault_dir=vault
    ) == "home"

    # An absolute-path NESTED Home.md (e.g. <vault>/08 - Misc/Home.md) with
    # vault_dir passed does NOT map to "home".
    assert cb._player_vault_section(
        _note(vault + "/08 - Misc/Home.md"), vault_dir=vault
    ) == "lore"

    # An absolute-path nested Home.md inside a MAPPED folder (e.g.
    # <vault>/02 - Cast/Home.md) with vault_dir passed maps to "cast" -
    # folder precedence stays intact ahead of the home special case.
    assert cb._player_vault_section(
        _note(vault + "/02 - Cast/Home.md"), vault_dir=vault
    ) == "cast"


def test_session_number_from_frontmatter_then_filename_then_none():
    assert cb._player_vault_session_number(_note("v/x.md", session=3)) == 3
    assert cb._player_vault_session_number(_note("v/01 - Chronicle/S05 - Deep.md")) == 5
    assert cb._player_vault_session_number(_note("v/Cast/Romi.md")) is None


def test_session_number_frontmatter_string_digits():
    # The frontmatter parser only coerces UNQUOTED digit runs to int
    # (_coerce_scalar); a quoted `session: "7"` arrives as the string "7".
    # Both representations must resolve to the same plain int.
    assert cb._player_vault_session_number(_note("v/x.md", session="7")) == 7


def test_session_number_frontmatter_wins_over_conflicting_filename():
    # Real vault notes carry BOTH a leading S<digits> filename AND a
    # `session:` frontmatter key (usually agreeing) - frontmatter must take
    # precedence even when the two disagree.
    note = _note("v/01 - Chronicle/S01 - Shadows Over Talmandor's Bounty.md", session=9)
    assert cb._player_vault_session_number(note) == 9


def test_session_number_zero_padded_filename_is_plain_int():
    n1 = cb._player_vault_session_number(
        _note("v/01 - Chronicle/S01 - Shadows Over Talmandor's Bounty.md")
    )
    n2 = cb._player_vault_session_number(_note("v/01 - Chronicle/S02 - Truth on the Wind.md"))
    assert (n1, n2) == (1, 2)
    assert type(n1) is int and type(n2) is int


def test_session_number_leading_S_required_not_anywhere_in_name():
    # An "S<digits>" that isn't at the very start of the filename must NOT
    # be mistaken for a session number.
    assert cb._player_vault_session_number(_note("v/Notes on S5 Recap.md")) is None


def test_session_number_non_numeric_frontmatter_falls_back_to_filename():
    note = _note("v/01 - Chronicle/S02 - Truth on the Wind.md", session="abc")
    assert cb._player_vault_session_number(note) == 2


def test_session_number_unicode_digit_frontmatter_does_not_raise():
    # "²" (superscript two) passes str.isdigit() but int("²")
    # raises ValueError - the guard must use isdecimal() (or a try/except)
    # so this falls through to the filename check instead of raising.
    note = _note("v/01 - Chronicle/S05 - Deep.md", session="²")
    assert cb._player_vault_session_number(note) == 5
    assert cb._player_vault_session_number(_note("v/Cast/Romi.md", session="²")) is None


def test_session_number_bool_frontmatter_falls_back_to_filename():
    # bool is an int subclass; session=True must NOT be read as session 1.
    note = _note("v/01 - Chronicle/S05 - Deep.md", session=True)
    assert cb._player_vault_session_number(note) == 5
    assert cb._player_vault_session_number(_note("v/Cast/Romi.md", session=True)) is None


def test_session_number_missing_everywhere_is_none():
    assert cb._player_vault_session_number(_note("v/01 - Chronicle/Chronicle.md")) is None
    assert cb._player_vault_session_number(_note("v/02 - Cast/Romi Bracken.md")) is None


# --- select_player_vault -----------------------------------------------


def test_select_player_vault_returns_expected_pages_with_sections():
    pages = cb.select_player_vault(FIXTURE)
    by_slug = {p["slug"]: p for p in pages}

    assert by_slug["home"]["section"] == "home"
    assert by_slug["the-docks"]["section"] == "recap"
    assert by_slug["romi-bracken"]["section"] == "cast"
    assert by_slug["the-intake"]["section"] == "atlas"
    assert by_slug["watch-notice"]["section"] == "handout"

    # _is_gm_meta skips: underscore-prefixed filename, and type: reference.
    assert "about-handouts" not in by_slug
    assert "about-this-chronicle" not in by_slug

    # The recap page carries the session number.
    assert by_slug["the-docks"]["session_updated"] == 1
    assert by_slug["the-docks"]["session_introduced"] == 1

    # A page with no session number anywhere carries neither session field.
    assert "session_updated" not in by_slug["romi-bracken"]
    assert "session_introduced" not in by_slug["romi-bracken"]


def test_select_player_vault_page_dict_shape_matches_gm_mode():
    pages = cb.select_player_vault(FIXTURE)
    page = next(p for p in pages if p["slug"] == "romi-bracken")
    assert page == {
        "slug": "romi-bracken",
        "section": "cast",
        "title": "Romi Bracken",
        "recipients": "all",
        "source": "content/romi-bracken.md",
        "body": cb.parse_note(FIXTURE / "02 - Cast" / "Romi Bracken.md")["body"],
    }


def test_select_player_vault_body_is_not_firewalled():
    # Bodies pass through AS AUTHORED - strip_gm_content must never run
    # here (this vault is already player-facing; stripping would delete
    # legitimate [!info]/[!abstract] content the GM wrote for players).
    pages = cb.select_player_vault(FIXTURE)
    romi = next(p for p in pages if p["slug"] == "romi-bracken")
    assert "[!info]" in romi["body"]
    assert "A shopkeeper." in romi["body"]


def test_select_player_vault_keeps_maps_subfolder():
    # Decision: _maps/ is underscore-prefixed but holds real player content
    # (a handout, per the real vault) - it must NOT be treated like a
    # GM-meta skip. Folder precedence still routes it via "04 - Atlas".
    pages = cb.select_player_vault(FIXTURE)
    by_slug = {p["slug"]: p for p in pages}
    assert "cavern-map" in by_slug
    assert by_slug["cavern-map"]["section"] == "atlas"


def test_select_player_vault_skips_site_and_dot_directories():
    # _Site/ (Obsidian Publish-plugin setup docs) and any dot-directory
    # (.obsidian/, .remember/ AI-agent session memory) are excluded at the
    # DIRECTORY level, before any per-note check - so a note inside them
    # with otherwise-ordinary, non-GM-meta frontmatter still never
    # publishes. Neither fixture note here is underscore-prefixed or
    # type: reference, so this only passes if the directory-level skip
    # itself is implemented (not merely riding on _is_gm_meta).
    pages = cb.select_player_vault(FIXTURE)
    titles = {p["title"] for p in pages}
    assert "Publish Config Note" not in titles
    assert "Session Memory Note" not in titles


def test_select_player_vault_home_section_via_absolute_path():
    # _load_notes produces ABSOLUTE paths. Without vault_dir passed through
    # to _player_vault_section at every call site, a top-level Home.md
    # would silently misresolve to "lore" instead of "home". Assert this
    # holds THROUGH select_player_vault (not merely at the
    # _player_vault_section unit level), against a genuinely absolute
    # fixture path.
    assert os.path.isabs(str(FIXTURE))
    pages = cb.select_player_vault(FIXTURE)
    home = next(p for p in pages if p["slug"] == "home")
    assert home["section"] == "home"


def test_select_player_vault_is_sorted_by_slug():
    pages = cb.select_player_vault(FIXTURE)
    slugs = [p["slug"] for p in pages]
    assert slugs == sorted(slugs)


# --- vault-wide asset resolution (Task 5) -------------------------------
#
# Player-vault mode's notes live anywhere in the tree, but Obsidian's own
# attachment model is FLAT: a note anywhere can write `![[intake-map.png]]`
# while the actual file sits in one shared folder (the fixture's
# `zz_Attachments/`, matching the real vault). GM-vault mode's
# `_resolve_asset_source`/`_find_asset` stay scoped to `Player Handouts/**`
# and are exercised, unmodified, by the `collect_assets` tests in
# test_chronicle_build.py - this section only covers the NEW vault-wide
# resolver and collect_assets' injectable-resolver hook.


def test_resolve_asset_source_anywhere_finds_basename_in_attachments_folder():
    # "04 - Atlas/The Intake.md" embeds ![[intake-map.png]]; the file
    # actually lives in the fixture's zz_Attachments/, not next to the note
    # and not under any "Player Handouts"-style tree.
    found = cb._resolve_asset_source_anywhere(FIXTURE, "intake-map.png")
    assert found == str(FIXTURE / "zz_Attachments" / "intake-map.png")


def test_resolve_asset_source_anywhere_dangling_ref_returns_none():
    assert cb._resolve_asset_source_anywhere(FIXTURE, "missing.png") is None


def test_resolve_asset_source_anywhere_excludes_dot_and_site_directories(tmp_path):
    # A same-named file that exists ONLY inside .obsidian/, .remember/, or
    # _Site/ must never resolve - those directories are treated as
    # non-content, same rule select_player_vault already applies to notes
    # (_player_vault_path_excluded), reused here rather than reimplemented.
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / ".obsidian" / "hidden.png").write_bytes(b"x")
    (vault / "_Site").mkdir()
    (vault / "_Site" / "hidden.png").write_bytes(b"x")
    (vault / ".remember").mkdir()
    (vault / ".remember" / "hidden.png").write_bytes(b"x")

    assert cb._resolve_asset_source_anywhere(vault, "hidden.png") is None

    # A file under a legitimately underscore-prefixed but non-excluded
    # folder (mirrors "_maps/" from select_player_vault) still resolves.
    (vault / "_maps").mkdir()
    (vault / "_maps" / "visible.png").write_bytes(b"x")
    assert cb._resolve_asset_source_anywhere(vault, "visible.png") == \
        str(vault / "_maps" / "visible.png")


def test_resolve_asset_source_anywhere_is_deterministic_first_match(tmp_path):
    # Two DIFFERENT files share a basename in different folders - the sorted
    # walk must return the SAME one on every call (alphabetically-first
    # directory wins), never depend on filesystem iteration order.
    vault = tmp_path / "vault"
    (vault / "Alpha").mkdir(parents=True)
    (vault / "Alpha" / "dup.png").write_bytes(b"a")
    (vault / "Zeta").mkdir()
    (vault / "Zeta" / "dup.png").write_bytes(b"z")

    results = {cb._resolve_asset_source_anywhere(vault, "dup.png") for _ in range(5)}
    assert results == {str(vault / "Alpha" / "dup.png")}


def test_resolve_asset_source_anywhere_traversal_ref_cannot_escape_vault(tmp_path):
    # A same-named file genuinely exists OUTSIDE the vault (simulating an
    # /etc/passwd-style escape target). No matter how the ref spells the
    # path - a `../`-climbing relative ref or a bare absolute path - the
    # resolver only ever walks inside vault_dir, so it must never find or
    # return the outside file.
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"outside-vault-bytes")

    vault = tmp_path / "vault"
    vault.mkdir()

    assert cb._resolve_asset_source_anywhere(vault, "../secret.png") is None
    assert cb._resolve_asset_source_anywhere(vault, "../../secret.png") is None
    assert cb._resolve_asset_source_anywhere(vault, str(outside)) is None


def test_collect_assets_player_vault_scope_resolves_zz_attachments(tmp_path):
    # End-to-end through collect_assets with the injectable resolver: pages
    # come straight from select_player_vault, exactly as a real player-vault
    # build would call it.
    pages = cb.select_player_vault(FIXTURE)
    out_assets = tmp_path / "assets"

    copied = cb.collect_assets(pages, FIXTURE, str(out_assets),
                                resolve_source=cb._resolve_asset_source_anywhere)

    assert "intake-map.png" in copied
    assert (out_assets / "intake-map.png").exists()


def test_collect_assets_player_vault_scope_dangling_ref_copies_nothing_and_logs(tmp_path, caplog):
    pages = [{"slug": "x", "body": "A cursed page. ![[missing.png]]"}]
    out_assets = tmp_path / "assets"

    with caplog.at_level(logging.WARNING, logger="chronicle_build"):
        copied = cb.collect_assets(pages, FIXTURE, str(out_assets),
                                    resolve_source=cb._resolve_asset_source_anywhere)

    assert copied == []
    assert not (out_assets / "missing.png").exists()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("missing.png" in w for w in warnings), warnings


def test_collect_assets_player_vault_scope_traversal_cannot_read_outside_vault(tmp_path):
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"outside-vault-bytes")

    vault = tmp_path / "vault"
    vault.mkdir()
    out_assets = tmp_path / "out" / "assets"

    pages = [
        {"slug": "a", "body": "![[../secret.png]]"},
        {"slug": "b", "body": "![[%s]]" % str(outside)},
    ]

    copied = cb.collect_assets(pages, str(vault), str(out_assets),
                                resolve_source=cb._resolve_asset_source_anywhere)

    assert copied == []
    assert not (out_assets / "secret.png").exists()


def test_collect_assets_default_resolver_is_unchanged_gm_scope(tmp_path):
    # Omitting resolve_source keeps collect_assets scoped to
    # "Player Handouts/**" (GM-vault mode) - a file living anywhere else in
    # the vault, even with a matching basename, must NOT resolve.
    vault = tmp_path / "vault"
    (vault / "Player Handouts" / "NPC Portraits").mkdir(parents=True)
    (vault / "Player Handouts" / "NPC Portraits" / "romi.png").write_bytes(b"x")
    (vault / "zz_Attachments").mkdir()
    (vault / "zz_Attachments" / "elsewhere.png").write_bytes(b"y")

    out_assets = tmp_path / "assets"
    pages = [
        {"slug": "romi", "body": "![[romi.png]]"},
        {"slug": "elsewhere", "body": "![[elsewhere.png]]"},
    ]

    copied = cb.collect_assets(pages, str(vault), str(out_assets))

    assert copied == ["romi.png"]
    assert (out_assets / "romi.png").exists()
    assert not (out_assets / "elsewhere.png").exists()


# --- build_player_vault(mode="player-vault") orchestration (Task 6) ----
#
# The GM-vault path (mode="gm-vault", the default) is entirely unmodified by
# this task -- test_chronicle_build.py's existing build_player_vault tests
# cover it and must keep passing byte-for-byte, untouched. This section
# covers only the NEW branch: mode="player-vault" wires select_player_vault
# (not select_entities/_select_pages) in, skips strip_gm_content entirely
# (bodies pass through as-authored), resolves assets vault-wide via
# _resolve_asset_source_anywhere, and STILL runs leak_check as the fatal,
# non-negotiable second firewall layer.


def test_build_player_vault_player_mode_happy_path(tmp_path):
    out = tmp_path / "out"
    result = cb.build_player_vault(FIXTURE, str(out), "sample", mode="player-vault")

    assert result["leaks"] == []

    manifest = result["manifest"]
    by_slug = {p["slug"]: p for p in manifest["pages"]}
    assert by_slug["home"]["section"] == "home"
    assert by_slug["the-docks"]["section"] == "recap"
    assert by_slug["romi-bracken"]["section"] == "cast"
    assert by_slug["the-intake"]["section"] == "atlas"
    assert by_slug["watch-notice"]["section"] == "handout"

    # The [!info] callout in Romi's body must survive -- strip_gm_content
    # must NOT have run in this mode.
    romi_body = (out / "content" / "romi-bracken.md").read_text(encoding="utf-8")
    assert "[!info]" in romi_body
    assert "A shopkeeper." in romi_body

    # [[The Intake]] resolves to a real page link, not degraded plain text.
    assert "/chronicle/page/the-intake" in romi_body

    # The embedded map asset was copied and resolved into the page body.
    assert (out / "assets" / "intake-map.png").exists()
    intake_body = (out / "content" / "the-intake.md").read_text(encoding="utf-8")
    assert "assets/intake-map.png" in intake_body

    # manifest.json was actually synced to out_dir on a clean build.
    assert (out / "manifest.json").exists()


def test_build_player_vault_player_mode_leak_aborts(tmp_path):
    out = tmp_path / "out"
    result = cb.build_player_vault(LEAK_FIXTURE, str(out), "sample", mode="player-vault")

    assert result["leaks"] != []
    assert any("danger" in o.lower() for o in result["leaks"])
    assert "leak" in result["review_summary"].lower()

    # No manifest, no content -- the leak-abort path must never sync
    # anything into out_dir.
    assert not (out / "manifest.json").exists()
    assert not (out / "content").exists()
    assert not (out / "assets").exists()


def test_build_player_vault_player_mode_never_touches_out_dir_on_leak(tmp_path):
    # Same data-safety guard as the GM-mode regression test
    # (test_build_player_vault_never_touches_out_dir_on_leak in
    # test_chronicle_build.py), proven independently for player-vault mode:
    # out_dir may be the GM's real, persistent Obsidian player vault and
    # must survive a leaky build completely untouched.
    out = tmp_path / "out"
    out.mkdir()
    sentinel = out / "GM_PRECIOUS.txt"
    sentinel.write_text("do not delete", encoding="utf-8")
    prior_manifest = out / "manifest.json"
    prior_manifest.write_text('{"prior": true}', encoding="utf-8")

    result = cb.build_player_vault(LEAK_FIXTURE, str(out), "sample", mode="player-vault")

    assert result["leaks"] != []
    assert sentinel.exists()
    assert sentinel.read_text(encoding="utf-8") == "do not delete"
    assert prior_manifest.read_text(encoding="utf-8") == '{"prior": true}'
    assert not (out / "content").exists()
    assert not (out / "assets").exists()


def test_build_player_vault_player_mode_slug_collision_reported_in_review_summary(tmp_path):
    # Carried-forward requirement: select_player_vault only signals a
    # dropped slug-collision page via log.warning, which the GM (this
    # tool's only user) never reads. build_player_vault must capture that
    # warning (the same _WarningCapture mechanism already used for
    # collect_assets' warnings) and fold it into review_summary.
    vault = tmp_path / "vault"
    cast_dir = vault / "02 - Cast"
    cast_dir.mkdir(parents=True)
    (cast_dir / "Romi Bracken.md").write_text(
        '---\ntype: npc\ntitle: "Romi Bracken"\n---\n# Romi Bracken\nThe original.\n',
        encoding="utf-8",
    )
    (cast_dir / "Also Romi.md").write_text(
        '---\ntype: npc\ntitle: "Romi Bracken"\n---\n# Romi Bracken\nA same-titled duplicate.\n',
        encoding="utf-8",
    )

    out = tmp_path / "out"
    result = cb.build_player_vault(str(vault), str(out), "sample", mode="player-vault")

    assert result["leaks"] == []
    # Only one of the two colliding notes survives into the manifest.
    slugs = [p["slug"] for p in result["manifest"]["pages"]]
    assert slugs.count("romi-bracken") == 1

    summary = result["review_summary"].lower()
    assert "collision" in summary
    assert "romi-bracken" in summary


def test_build_player_vault_player_mode_asset_warning_reported_in_review_summary(tmp_path):
    # collect_assets' own not-found warning must also still reach
    # review_summary in this mode (same capture, not a second code path).
    vault = tmp_path / "vault"
    cast_dir = vault / "02 - Cast"
    cast_dir.mkdir(parents=True)
    (cast_dir / "Ghost.md").write_text(
        '---\ntype: npc\ntitle: "Ghost"\n---\n# Ghost\n![[missing.png]]\n',
        encoding="utf-8",
    )

    out = tmp_path / "out"
    result = cb.build_player_vault(str(vault), str(out), "sample", mode="player-vault")

    assert result["leaks"] == []
    assert "missing.png" in result["review_summary"]


def test_build_player_vault_player_mode_default_mode_is_gm_vault():
    # The `mode` param defaults to "gm-vault" -- omitting it entirely must
    # behave exactly as before this parameter existed. Calling with the
    # player-vault fixture in default (gm-vault) mode must NOT select any
    # pages via select_player_vault's rules (no session_notes/chronicle
    # overrides exist in this fixture for select_entities to find).
    with tempfile.TemporaryDirectory() as out:
        result = cb.build_player_vault(FIXTURE, out, "sample")
        assert result["manifest"]["pages"] == []


def test_build_player_vault_unknown_mode_raises(tmp_path):
    with pytest.raises(ValueError):
        cb.build_player_vault(FIXTURE, str(tmp_path / "out"), "sample", mode="bogus-mode")
