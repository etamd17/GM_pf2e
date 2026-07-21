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


def _romi_body():
    return cb.parse_note(FIXTURE / "NPCs" / "Romi Bracken.md")["body"]


def test_strip_removes_all_gm_callouts():
    out = cb.strip_gm_content(_romi_body())
    pb = out["player_body"]
    # [!danger] gone, content and marker both
    assert "[!danger]" not in pb
    assert "sacrifice the party" not in pb
    assert "Camazotz" not in pb
    # [!info] gone
    assert "[!info]" not in pb
    assert "AC 22" not in pb
    # [!warning] gone
    assert "[!warning]" not in pb
    assert "escalate the temptation" not in pb


def test_strip_keeps_player_callouts_verbatim():
    out = cb.strip_gm_content(_romi_body())
    pb = out["player_body"]
    # quote kept with its callout syntax intact for the PR1 renderer
    assert "> [!quote] Recruitment Pitch" in pb
    assert "incredible soldiers for a cause greater than yourselves" in pb
    # example kept
    assert "> [!example] Handout Fragment" in pb
    assert "A pressed flower" in pb
    # plain narration outside any callout survives
    assert "A warm shopkeeper with an easy smile" in pb


def test_strip_harvests_check_and_question_to_mysteries():
    out = cb.strip_gm_content(_romi_body())
    kinds = {(m["kind"]) for m in out["mysteries"]}
    assert "fact" in kinds and "question" in kinds
    fact = next(m for m in out["mysteries"] if m["kind"] == "fact")
    question = next(m for m in out["mysteries"] if m["kind"] == "question")
    assert "runs the Intake" in fact["text"]
    assert "hiding something behind the sealed door" in question["text"]
    # harvested callouts are removed from the player body
    assert "[!check]" not in out["player_body"]
    assert "[!question]" not in out["player_body"]


def test_strip_pulls_abstract_into_recap_seed():
    session_body = cb.parse_note(
        FIXTURE / "Sessions" / "Session - April 21 2026.md")["body"]
    out = cb.strip_gm_content(session_body)
    assert out["recap_seed"] is not None
    assert "breached the Intake Entrance and met Romi Bracken" in out["recap_seed"]
    assert "[!abstract]" not in out["player_body"]
    # the danger block in the same note is still stripped
    assert "azlanti tech" not in out["player_body"]
    assert "[!danger]" not in out["player_body"]


def test_strip_removes_obsidian_and_html_comments():
    out = cb.strip_gm_content(_romi_body())
    assert "%%" not in out["player_body"]
    assert "reroll his reaction" not in out["player_body"]
    assert "<!--" not in out["player_body"]
    assert "cross-link this to the Camazotz arc" not in out["player_body"]


def test_strip_unknown_callout_is_dropped_by_default():
    body = "> [!secret] hush\n> the vault code is 1234\n\nvisible line\n"
    out = cb.strip_gm_content(body)
    assert "1234" not in out["player_body"]
    assert "[!secret]" not in out["player_body"]
    assert "visible line" in out["player_body"]


def test_strip_no_recap_returns_none():
    out = cb.strip_gm_content("plain body, no callouts\n")
    assert out["recap_seed"] is None
    assert out["mysteries"] == []
    assert "plain body" in out["player_body"]


def test_strip_adversarial_danger_variants_never_leak():
    # multi-line danger body
    body = (
        "> [!danger] Multi\n"
        "> line one secretcode\n"
        "> line two secretcode\n"
        "\nsafe line\n"
    )
    out = cb.strip_gm_content(body)
    assert "secretcode" not in out["player_body"]
    assert "safe line" in out["player_body"]

    # danger at EOF with no trailing newline
    body_eof = "> [!danger] End\n> the final secret is EOFSECRET"
    out_eof = cb.strip_gm_content(body_eof)
    assert "EOFSECRET" not in out_eof["player_body"]

    # danger immediately followed by another callout (no blank line)
    body_adjacent = (
        "> [!danger] First\n"
        "> ADJACENTSECRET\n"
        "> [!quote] Second\n"
        "> visible quote text\n"
    )
    out_adjacent = cb.strip_gm_content(body_adjacent)
    assert "ADJACENTSECRET" not in out_adjacent["player_body"]
    assert "visible quote text" in out_adjacent["player_body"]

    # danger body containing '>' and a fake nested callout marker
    body_nested = (
        "> [!danger] Nested\n"
        "> the code is > 42 and also [!quote] fake\n"
        "> NESTEDSECRET\n"
        "\nafter\n"
    )
    out_nested = cb.strip_gm_content(body_nested)
    assert "NESTEDSECRET" not in out_nested["player_body"]
    assert "after" in out_nested["player_body"]

    # a KEPT callout (quote) immediately followed by [!danger] with no blank
    # line between them: the danger body must not be swallowed into the
    # quote's continuation lines and kept verbatim alongside it.
    body_quote_then_danger = (
        "> [!quote] Hello\n"
        "> visible quote line\n"
        "> [!danger] secret\n"
        "> QUOTEADJACENTSECRET\n"
    )
    out_qd = cb.strip_gm_content(body_quote_then_danger)
    assert "QUOTEADJACENTSECRET" not in out_qd["player_body"]
    assert "[!danger]" not in out_qd["player_body"]
    assert "visible quote line" in out_qd["player_body"]


def test_strip_comment_line_does_not_sever_danger_block():
    # Regression: a bare comment line inside a [!danger] block used to be
    # stripped GLOBALLY before the block walk, collapsing the boundary and
    # leaking the tail of the block (no marker, no callout syntax at all).
    body = (
        "> [!danger] True Motive\n"
        "> Romi serves Camazotz.\n"
        "<!-- reminder: escalate here -->\n"
        "> and intends to sacrifice the party at the door.\n"
    )
    out = cb.strip_gm_content(body)
    assert "sacrifice the party" not in out["player_body"]
    assert "Camazotz" not in out["player_body"]

    # Same shape with an Obsidian %%...%% comment splitting the block.
    body_obsidian = (
        "> [!danger] True Motive\n"
        "> Romi serves Camazotz.\n"
        "%% reminder: escalate here %%\n"
        "> and intends to sacrifice the party at the door.\n"
    )
    out2 = cb.strip_gm_content(body_obsidian)
    assert "sacrifice the party" not in out2["player_body"]
    assert "Camazotz" not in out2["player_body"]


def test_strip_non_alpha_callout_kinds_are_stripped():
    # Regression: the callout marker regex only matched [A-Za-z]+ kinds, so
    # anything with a digit/underscore/hyphen in it (or any custom kind not
    # explicitly on a blocklist) bypassed the firewall and leaked RAW.
    for kind, secret in [
        ("spoiler_alert", "5678"),
        ("lore-bomb", "LOREBOMBSECRET"),
        ("twist_reveal", "TWISTSECRET"),
        ("secret", "SECRETVALUE"),
        ("gm", "GMONLYVALUE"),
    ]:
        body = f"> [!{kind}] Hush\n> the vault code is {secret}\n"
        out = cb.strip_gm_content(body)
        assert secret not in out["player_body"], kind
        assert f"[!{kind}]" not in out["player_body"], kind


def test_strip_multiline_comment_does_not_sever_danger_block():
    # Regression: _COMMENT_ONLY_LINE only matches a comment that opens AND
    # closes on the SAME physical line. A multi-line comment (the open
    # delimiter on one line, the close on a later line) inside a
    # [!danger] block was invisible to that check, so the continuation
    # scan stopped at the opening line and the block's tail leaked with
    # no marker at all.
    body = (
        "> [!danger] True Motive\n"
        "> Romi serves Camazotz.\n"
        "<!--\n"
        "reminder: escalate here\n"
        "-->\n"
        "> and intends to sacrifice the party at the door.\n"
    )
    out = cb.strip_gm_content(body)
    assert "sacrifice the party" not in out["player_body"]
    assert "Camazotz" not in out["player_body"]

    # Same shape with a multi-line Obsidian %% ... %% comment.
    body_obsidian = (
        "> [!danger] True Motive\n"
        "> Romi serves Camazotz.\n"
        "%%\n"
        "GM aside spanning\n"
        "two lines\n"
        "%%\n"
        "> and intends to sacrifice the party at the door.\n"
    )
    out2 = cb.strip_gm_content(body_obsidian)
    assert "sacrifice the party" not in out2["player_body"]
    assert "Camazotz" not in out2["player_body"]


def test_strip_unterminated_multiline_comment_absorbs_rest_of_block():
    # Fail-safe: an opened multi-line comment that never closes must not
    # leak anything that follows it. With no closing delimiter, nothing
    # ever ends the continuation scan, so the rest of the block (and any
    # trailing lines) is absorbed into the block and dropped rather than
    # leaked unprotected.
    body = (
        "> [!danger] Unterminated\n"
        "> Romi serves Camazotz.\n"
        "<!--\n"
        "this comment never closes\n"
        "> and neither does the secret UNTERMINATEDSECRET\n"
    )
    out = cb.strip_gm_content(body)
    assert "UNTERMINATEDSECRET" not in out["player_body"]
    assert "Camazotz" not in out["player_body"]


def test_strip_allowlist_keep_and_harvest_kinds_still_work():
    # Non-regression: the allowlist must still keep quote/example verbatim
    # and still harvest check/question/abstract.
    out = cb.strip_gm_content(_romi_body())
    pb = out["player_body"]
    assert "> [!quote] Recruitment Pitch" in pb
    assert "> [!example] Handout Fragment" in pb
    kinds = {m["kind"] for m in out["mysteries"]}
    assert "fact" in kinds and "question" in kinds

    session_body = cb.parse_note(
        FIXTURE / "Sessions" / "Session - April 21 2026.md")["body"]
    seeded = cb.strip_gm_content(session_body)
    assert seeded["recap_seed"] is not None
