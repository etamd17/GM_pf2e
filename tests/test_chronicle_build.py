import pathlib
import re as _re

from tools import chronicle_build as cb

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "gm_vault_sample"

SLUG_RE = _re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")


def test_fixture_vault_is_present_and_shaped():
    assert FIXTURE.is_dir()
    session = FIXTURE / "Sessions" / "Session - April 21 2026.md"
    romi = FIXTURE / "NPCs" / "Romi Bracken.md"
    alzira = FIXTURE / "NPCs" / "Alzira Vane.md"
    area = FIXTURE / "Areas" / "C2 Intake Entrance.md"
    letter = FIXTURE / "Player Handouts" / "Letters & Journals" / "Romi's Note.md"
    for p in (session, romi, alzira, area, letter):
        assert p.is_file(), p

    session_text = session.read_text(encoding="utf-8")
    assert "session_number: 5" in session_text
    assert "npcs_encountered: [Romi Bracken, Cult Patrol Guards]" in session_text
    assert "[!abstract]" in session_text

    romi_text = romi.read_text(encoding="utf-8")
    # the NPC note must exercise every firewall branch
    for token in ("[!danger]", "[!info]", "[!warning]", "[!quote]",
                  "[!check]", "[!question]", "[!example]", "%%", "<!--"):
        assert token in romi_text, token

    assert "chronicle: false" in alzira.read_text(encoding="utf-8")
    assert "[!danger]" in area.read_text(encoding="utf-8")  # planted for leak_check


def test_parse_note_splits_frontmatter_and_body():
    note = cb.parse_note(FIXTURE / "NPCs" / "Romi Bracken.md")
    fm = note["frontmatter"]
    assert fm["type"] == "npc"
    assert fm["name"] == "Romi Bracken"            # quotes stripped
    assert fm["role"] == "Cult leader (revealed S4)"
    assert fm["chronicle"] is True                 # bool coercion
    assert fm["tags"] == ["npc", "cult", "book1", "recurring"]  # flow list
    assert note["body"].startswith("\n# Romi Bracken") or \
           note["body"].lstrip().startswith("# Romi Bracken")
    assert note["path"].endswith("Romi Bracken.md")


def test_parse_note_coerces_ints_and_flow_lists():
    fm = cb.parse_note(FIXTURE / "Sessions" / "Session - April 21 2026.md")["frontmatter"]
    assert fm["session_number"] == 5               # int, not "5"
    assert fm["areas_covered"] == ["C2", "C3", "C11"]
    assert fm["npcs_encountered"] == ["Romi Bracken", "Cult Patrol Guards"]
    assert fm["status"] == "completed"


def test_parse_note_tolerates_missing_frontmatter(tmp_path):
    p = tmp_path / "plain.md"
    p.write_text("# Just a heading\n\nno yaml here\n", encoding="utf-8")
    note = cb.parse_note(p)
    assert note["frontmatter"] == {}
    assert note["body"] == "# Just a heading\n\nno yaml here\n"


def test_parse_note_supports_block_lists(tmp_path):
    p = tmp_path / "block.md"
    p.write_text("---\ntags:\n  - alpha\n  - beta\n---\nbody\n", encoding="utf-8")
    fm = cb.parse_note(p)["frontmatter"]
    assert fm["tags"] == ["alpha", "beta"]


def test_slugify_basic():
    assert cb.slugify("C2 Intake Entrance") == "c2-intake-entrance"
    assert cb.slugify("Romi Bracken") == "romi-bracken"


def test_slugify_strips_punctuation_and_apostrophes():
    assert cb.slugify("Go'el, the Warpriest!") == "go-el-the-warpriest"
    assert cb.slugify("  --Letters & Journals--  ") == "letters-journals"


def test_slugify_empty_and_symbol_only_fall_back_to_page():
    assert cb.slugify("") == "page"
    assert cb.slugify("!!!") == "page"
    assert cb.slugify(None) == "page"


def test_slugify_always_matches_pr1_pattern():
    for title in ["C2 Intake Entrance", "Romi Bracken", "!!!", "",
                  "x" * 200, "9 Lives", "-leading-dash-"]:
        assert SLUG_RE.match(cb.slugify(title)), title
