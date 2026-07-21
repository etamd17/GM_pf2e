import pathlib
import re as _re
import textwrap

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


# ---------------------------------------------------------------------------
# PR0 firewall REDESIGN acceptance tests.
#
# The old strip_gm_content() was a hand-rolled multi-line-comment state
# machine layered on top of the callout-block walk, and three incremental
# patches on top of it each closed one leak while leaving another opening.
# These tests reproduce every known leak shape with a unique sentinel per
# case, so absence can be asserted precisely. Each one must be GREEN under
# the redesigned strip_gm_content (comments stripped globally up front,
# simple block boundaries, blanket bare-'>' stripping) even though several
# of them were RED under the old state machine.
# ---------------------------------------------------------------------------

def _mysteries_text(out):
    return " ".join(m["text"] for m in out["mysteries"])


def test_leak_repro_01_single_line_comment_severs_danger_block():
    # A single-line comment sits between two '>' continuation lines of a
    # [!danger] block. The comment-stripped body leaves the tail as a bare
    # '>' line with no header of its own - step 3's blanket bare-blockquote
    # strip must catch it even though it's no longer joined to the original
    # block.
    body = (
        "> [!danger] True Motive\n"
        "> Romi serves Camazotz.\n"
        "<!-- reminder: escalate here -->\n"
        "> and intends to sacrifice the party at LEAK1_TAILSECRET_9f3a.\n"
    )
    out = cb.strip_gm_content(body)
    assert "LEAK1_TAILSECRET_9f3a" not in out["player_body"]
    assert "LEAK1_TAILSECRET_9f3a" not in _mysteries_text(out)


def test_leak_repro_02_multiline_html_comment_severs_danger_block():
    body = (
        "> [!danger] True Motive\n"
        "> Romi serves Camazotz.\n"
        "<!--\n"
        "GM aside spanning lines\n"
        "-->\n"
        "> and intends to sacrifice the party at LEAK2_TAILSECRET_7bd1.\n"
    )
    out = cb.strip_gm_content(body)
    assert "LEAK2_TAILSECRET_7bd1" not in out["player_body"]
    assert "LEAK2_TAILSECRET_7bd1" not in _mysteries_text(out)


def test_leak_repro_03_multiline_percent_comment_severs_danger_block():
    body = (
        "> [!danger] True Motive\n"
        "> Romi serves Camazotz.\n"
        "%%\n"
        "GM aside spanning lines\n"
        "%%\n"
        "> and intends to sacrifice the party at LEAK3_TAILSECRET_c44e.\n"
    )
    out = cb.strip_gm_content(body)
    assert "LEAK3_TAILSECRET_c44e" not in out["player_body"]
    assert "LEAK3_TAILSECRET_c44e" not in _mysteries_text(out)


def test_leak_repro_04_unterminated_html_comment_strips_everything_after():
    # Fail-safe: an opened multi-line comment that never closes must not
    # leak anything that follows it, even when Step 1's regex can't match
    # (no closing delimiter anywhere) and the raw "<!--" survives into the
    # block walk untouched.
    body = (
        "> [!danger] Unterminated\n"
        "> Romi serves Camazotz.\n"
        "<!--\n"
        "this comment never closes\n"
        "> and neither does the secret LEAK4_TAILSECRET_02aa\n"
    )
    out = cb.strip_gm_content(body)
    assert "LEAK4_TAILSECRET_02aa" not in out["player_body"]
    assert "LEAK4_TAILSECRET_02aa" not in _mysteries_text(out)
    assert "Camazotz" not in out["player_body"]


def test_leak_repro_05_non_alpha_custom_kinds_stripped():
    for kind, secret in [
        ("spoiler_alert", "LEAK5A_5678x"),
        ("lore-bomb", "LEAK5B_LOREBOMB"),
        ("twist_reveal", "LEAK5C_TWISTSECRET"),
        ("secret", "LEAK5D_SECRETVALUE"),
        ("gm", "LEAK5E_GMONLYVALUE"),
    ]:
        body = f"> [!{kind}] Hush\n> the vault code is {secret}\n"
        out = cb.strip_gm_content(body)
        assert secret not in out["player_body"], kind
        assert secret not in _mysteries_text(out), kind
        assert f"[!{kind}]" not in out["player_body"], kind


def test_leak_repro_06_sameline_marker_and_opener_after_kept_block():
    # A [!quote] block immediately followed (no blank line) by a [!danger]
    # header whose OWN line opens an inline "<!--" comment. The old code
    # only tracked an unterminated comment opened on a CONTINUATION line,
    # never one opened on the header line itself, so the header's embedded
    # opener was invisible to the state machine and the tail leaked
    # verbatim once it fell out of any recognized block.
    body = (
        "> [!quote] Safe\n"
        "> visible quote text\n"
        "> [!danger] Secret <!--\n"
        "comment body\n"
        "-->\n"
        "> and does LEAK6_SAMELINE_d91c\n"
    )
    out = cb.strip_gm_content(body)
    assert "LEAK6_SAMELINE_d91c" not in out["player_body"]
    assert "LEAK6_SAMELINE_d91c" not in _mysteries_text(out)
    assert "> [!quote] Safe" in out["player_body"]
    assert "visible quote text" in out["player_body"]


def test_leak_repro_07_title_embedded_opener_strips_blockquote_tail():
    # Same header-embedded-opener bug as #06, without a preceding kept
    # block. Trailing PLAIN prose after a closed comment is the accepted
    # residual (see strip_gm_content's docstring) - the secret here sits on
    # a '>'-prefixed line, which must still be caught by the blanket bare
    # blockquote strip.
    body = (
        "> [!danger] True Motive <!--\n"
        "comment aside\n"
        "-->\n"
        "> and intends to LEAK7_TITLEOPEN_44bb\n"
    )
    out = cb.strip_gm_content(body)
    assert "LEAK7_TITLEOPEN_44bb" not in out["player_body"]
    assert "LEAK7_TITLEOPEN_44bb" not in _mysteries_text(out)


def test_leak_repro_08_kept_block_then_danger_no_blank_line():
    body = (
        "> [!quote] X\n"
        "> line\n"
        "> [!danger] Y\n"
        "> secret LEAK8_ADJACENT_11ee\n"
    )
    out = cb.strip_gm_content(body)
    assert "LEAK8_ADJACENT_11ee" not in out["player_body"]
    assert "LEAK8_ADJACENT_11ee" not in _mysteries_text(out)
    assert "> [!quote] X" in out["player_body"]
    assert "> line" in out["player_body"]


def test_leak_repro_09_bare_blockquote_with_no_header_is_stripped():
    # A '>' blockquote with no [!kind] header at all was never recognized
    # by the old callout walk (it only special-cased header lines), so it
    # fell straight through to the main loop's "not a callout" branch and
    # was appended to player_body untouched, '>' and all.
    body = "> just a regular blockquote mentioning LEAK9_BAREQUOTE_ff02\n\nvisible line\n"
    out = cb.strip_gm_content(body)
    assert "LEAK9_BAREQUOTE_ff02" not in out["player_body"]
    assert "LEAK9_BAREQUOTE_ff02" not in _mysteries_text(out)
    assert "visible line" in out["player_body"]


# ---------------------------------------------------------------------------
# Adversarial review round 2: nested (`>>`) and indented (leading-whitespace)
# callouts bypass both the header regex (exactly one leading '>') and the
# block-walk's blockquote-line test (`line.startswith(">")`, exactly one),
# so their bodies ride into whatever surface is open (player_body/mysteries/
# recap_seed) or leak as untouched prose. Also restores header-only-callout
# harvesting (a [!check]/[!question]/[!abstract] written entirely on the
# header line, no '>' continuation at all).
# ---------------------------------------------------------------------------

def test_leak_repro_10_nested_danger_bypasses_into_player_body():
    body = (
        "> [!quote] Safe\n"
        "> visible\n"
        ">> [!danger] Nested\n"
        ">> serves NESTED_SECRET_777\n"
    )
    out = cb.strip_gm_content(body)
    assert "NESTED_SECRET_777" not in out["player_body"]
    assert "NESTED_SECRET_777" not in _mysteries_text(out)
    assert "visible" in out["player_body"]


def test_leak_repro_11_nested_danger_bypasses_into_mysteries():
    body = (
        "> [!check] Clue\n"
        "> a torn note\n"
        ">> [!danger] Answer\n"
        ">> killer is CHECKLEAK_4471\n"
    )
    out = cb.strip_gm_content(body)
    assert "CHECKLEAK_4471" not in _mysteries_text(out)
    assert "CHECKLEAK_4471" not in out["player_body"]


def test_leak_repro_12_nested_danger_bypasses_into_recap_seed():
    body = (
        "> [!abstract] Clue\n"
        "> the party learned a torn note\n"
        ">> [!danger] Answer\n"
        ">> killer is RECAPLEAK_8820\n"
    )
    out = cb.strip_gm_content(body)
    assert out["recap_seed"] is not None
    assert "RECAPLEAK_8820" not in out["recap_seed"]
    assert "RECAPLEAK_8820" not in out["player_body"]


def test_leak_repro_13_indented_callout_bypasses_detection():
    body = " > [!danger] Indented\n > serves INDENTED_LEAK_9021\n"
    out = cb.strip_gm_content(body)
    assert "INDENTED_LEAK_9021" not in out["player_body"]
    assert "INDENTED_LEAK_9021" not in _mysteries_text(out)


def test_leak_repro_14_nested_custom_kind_is_stripped():
    body = (
        "> [!quote] Safe\n"
        "> visible\n"
        ">> [!spoiler_alert] secret\n"
        ">> LEAKCUSTOM_9999\n"
    )
    out = cb.strip_gm_content(body)
    assert "LEAKCUSTOM_9999" not in out["player_body"]
    assert "[!spoiler_alert]" not in out["player_body"]
    assert "visible" in out["player_body"]


def test_header_only_question_is_harvested():
    body = "> [!question] Is Romi lying HEADERONLY_Q?\n"
    out = cb.strip_gm_content(body)
    assert "HEADERONLY_Q" in _mysteries_text(out)
    assert "[!question]" not in out["player_body"]


def test_header_only_abstract_seeds_recap():
    body = "> [!abstract] Party met Romi HEADERONLY_ABS.\n"
    out = cb.strip_gm_content(body)
    assert out["recap_seed"] is not None
    assert "HEADERONLY_ABS" in out["recap_seed"]
    assert "[!abstract]" not in out["player_body"]


# ---------------------------------------------------------------------------
# PR0 task 04: comment-splice leak. A comment that WRAPS a callout marker
# token mid-line ("> <!--[!danger] Secret Title-->real secret") deletes the
# marker along with the comment, leaving trailing secret prose on what is
# still a kept/harvested blockquote continuation line - because comment
# removal happens globally, before the block walk ever sees a "[!danger]"
# header to reject. A marker-bearing comment must nuke its ENTIRE physical
# line(s), not just the comment substring, whenever that would otherwise
# leave blockquote content behind.
# ---------------------------------------------------------------------------

def test_leak_repro_15_comment_splice_marker_leaks_into_player_body():
    body = (
        "> [!quote] Safe\n"
        "> visible1\n"
        "> <!--[!danger] Secret Title-->real secret DISGUISE_999\n"
        "> visible2\n"
    )
    out = cb.strip_gm_content(body)
    assert "DISGUISE_999" not in out["player_body"]
    assert "DISGUISE_999" not in _mysteries_text(out)


def test_leak_repro_16_comment_splice_marker_leaks_into_mysteries():
    body = (
        "> [!check] Clue\n"
        "> visible1\n"
        "> <!--[!danger] Secret Title-->real secret MYSTLEAK_DISGUISE_1\n"
        "> visible2\n"
    )
    out = cb.strip_gm_content(body)
    assert "MYSTLEAK_DISGUISE_1" not in _mysteries_text(out)
    assert "MYSTLEAK_DISGUISE_1" not in out["player_body"]


def test_leak_repro_17_comment_splice_marker_leaks_into_recap_seed():
    body = (
        "> [!abstract] Summary\n"
        "> visible1\n"
        "> <!--[!danger] Secret Title-->real secret RECAPLEAK_DISGUISE_1\n"
        "> visible2\n"
    )
    out = cb.strip_gm_content(body)
    assert out["recap_seed"] is not None
    assert "RECAPLEAK_DISGUISE_1" not in out["recap_seed"]
    assert "RECAPLEAK_DISGUISE_1" not in out["player_body"]


def test_leak_repro_18_percent_comment_splice_marker_leaks():
    body = (
        "> [!quote] Safe\n"
        "> visible1\n"
        "> %%[!danger] Secret Title%%real secret PCTDISGUISE_1\n"
        "> visible2\n"
    )
    out = cb.strip_gm_content(body)
    assert "PCTDISGUISE_1" not in out["player_body"]
    assert "PCTDISGUISE_1" not in _mysteries_text(out)


def test_leak_repro_19_multiline_comment_splice_marker_leaks():
    body = (
        "> [!quote] Safe\n"
        "> visible1\n"
        "> <!--\n"
        "[!danger] X\n"
        "-->secret MULTILINEDISGUISE_1\n"
        "> visible2\n"
    )
    out = cb.strip_gm_content(body)
    assert "MULTILINEDISGUISE_1" not in out["player_body"]
    assert "MULTILINEDISGUISE_1" not in _mysteries_text(out)


def test_normal_comment_without_marker_still_keeps_quote_text():
    # Non-regression: a comment with NO callout marker inside it is just an
    # ordinary author note. It must still be stripped normally, keeping the
    # rest of the line (and the quote) intact.
    body = "> [!quote] text <!-- author note -->\n"
    out = cb.strip_gm_content(body)
    assert "text" in out["player_body"]
    assert "[!quote]" in out["player_body"]
    assert "author note" not in out["player_body"]
    assert "<!--" not in out["player_body"]


# --- select_entities -------------------------------------------------------


def test_select_entities_against_fixture_vault():
    # Derived directly from the checked-in fixture (tests/fixtures/gm_vault_sample):
    # Session 5 is `status: completed` and encountered Romi Bracken (whose NPC
    # note also carries `chronicle: true`) and covers areas C2/C3/C11. Alzira
    # Vane is neither encountered in that session nor force-included - her
    # `chronicle: false` is a belt-and-suspenders exclusion.
    result = cb.select_entities(FIXTURE)

    assert "Romi Bracken" in result["npcs"]
    assert "Alzira Vane" not in result["npcs"]

    assert {"C2", "C3", "C11"} <= result["areas"]

    assert len(result["sessions"]) == 1
    assert result["sessions"][0]["frontmatter"]["session_number"] == 5
    nums = [n["frontmatter"]["session_number"] for n in result["sessions"]]
    assert nums == sorted(nums)


def _write_vault_note(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text), encoding="utf-8")


def _make_override_vault(tmp_path):
    # Two completed sessions (out of number order on disk) + one in_progress.
    _write_vault_note(tmp_path / "Session - April 21 2026.md", """\
        ---
        type: session_notes
        session_number: 5
        status: completed
        npcs_encountered: [Romi Bracken, Cult Patrol Guards]
        areas_covered: [C2, C3]
        ---
        > [!abstract] The party met Romi at the intake door.
        """)
    _write_vault_note(tmp_path / "Session - April 14 2026.md", """\
        ---
        type: session_notes
        session_number: 4
        status: complete
        npcs_encountered: [Alzira]
        areas_covered: [C1]
        ---
        Body.
        """)
    _write_vault_note(tmp_path / "Session - April 28 2026.md", """\
        ---
        type: session_notes
        session_number: 6
        status: in_progress
        npcs_encountered: [The Hidden Patron]
        areas_covered: [C9]
        ---
        Body.
        """)
    # NPC force-excluded even though encountered.
    _write_vault_note(tmp_path / "NPCs" / "Alzira.md", """\
        ---
        type: npc
        name: Alzira
        chronicle: false
        ---
        Body.
        """)
    # NPC force-included even though never encountered.
    _write_vault_note(tmp_path / "NPCs" / "Old Salk.md", """\
        ---
        type: npc
        name: Old Salk
        chronicle: true
        ---
        Body.
        """)
    # Location force-included by area_code.
    _write_vault_note(tmp_path / "Areas" / "C11 Sky Dock.md", """\
        ---
        type: location
        area_code: C11
        name: Sky Dock
        chronicle: true
        ---
        Body.
        """)
    return tmp_path


def test_select_entities_proposes_encountered_and_honors_overrides(tmp_path):
    result = cb.select_entities(_make_override_vault(tmp_path))

    # Encountered-in-completed-session NPCs are proposed.
    assert "Romi Bracken" in result["npcs"]
    assert "Cult Patrol Guards" in result["npcs"]
    # chronicle:false force-excludes even though encountered.
    assert "Alzira" not in result["npcs"]
    # chronicle:true force-includes even though never encountered.
    assert "Old Salk" in result["npcs"]
    # in_progress session contributes nothing.
    assert "The Hidden Patron" not in result["npcs"]

    assert "C2" in result["areas"] and "C3" in result["areas"]
    assert "C1" in result["areas"]          # from the other completed session
    assert "C11" in result["areas"]         # force-included location
    assert "C9" not in result["areas"]      # in_progress session excluded

    # Only completed sessions, sorted by session_number.
    nums = [n["frontmatter"]["session_number"] for n in result["sessions"]]
    assert nums == [4, 5]
