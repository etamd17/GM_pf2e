import os
import pathlib

from tools import chronicle_build as cb

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "player_vault_sample"


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
