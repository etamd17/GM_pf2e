from tools import chronicle_build as cb


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
