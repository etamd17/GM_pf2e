import io
import logging
import os
import pathlib
import re as _re
import sys
import textwrap
import zipfile

import pytest

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


# ---------------------------------------------------------------------------
# Final-review fix M6: the callout header's TITLE text ("Confirmed",
# "Suspected", "Previously On", ...) must NOT ride along as the first line
# of the harvested mystery `text` / `recap_seed` -- only the callout BODY
# (its depth-1 continuation lines) is story content. A header-only callout
# (no continuation at all) still falls back to its title as the content,
# since that's the only text the GM wrote.
# ---------------------------------------------------------------------------


def test_strip_check_harvest_drops_callout_title_keeps_body_only():
    out = cb.strip_gm_content(_romi_body())
    fact = next(m for m in out["mysteries"] if m["kind"] == "fact")
    question = next(m for m in out["mysteries"] if m["kind"] == "question")
    assert fact["text"] == "The party knows Romi runs the Intake and greeted them by name."
    assert question["text"] == "The party suspects Romi is hiding something behind the sealed door."
    assert "Confirmed" not in fact["text"]
    assert "Suspected" not in question["text"]


def test_strip_abstract_recap_seed_drops_callout_title_keeps_body_only():
    session_body = cb.parse_note(
        FIXTURE / "Sessions" / "Session - April 21 2026.md")["body"]
    out = cb.strip_gm_content(session_body)
    assert out["recap_seed"] == (
        "The party breached the Intake Entrance and met Romi Bracken at the glowing door."
    )
    assert "Previously On" not in out["recap_seed"]


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


# ---------------------------------------------------------------------------
# Whole-branch review C1: REVERSE nesting leak. A keep/harvest callout
# ([!quote]/[!example]/[!check]/[!question]/[!abstract]) nested INSIDE a GM
# (strip) callout ([!danger]/[!info]/[!tip]/[!warning]) is depth >= 2, but the
# block walk is depth-flat: it treats the nested ">> [!kind]" header as the
# START of a brand-new block and decides keep/harvest purely on that header's
# OWN kind, with no memory that it's sitting inside an enclosing GM block.
# leak_check doesn't catch this either, because no [!danger]/[!secret]/[!gm]
# marker survives on the kept/harvested text. Fix: keep/harvest only fires for
# a depth-1 (top-level) callout header; any header at depth >= 2 is stripped
# unconditionally, regardless of its own kind.
# ---------------------------------------------------------------------------

def test_leak_repro_20_nested_quote_inside_danger_leaks_into_player_body():
    body = (
        "> [!danger] Secret trap\n"
        "> > [!quote] Read when sprung\n"
        "> > The ceiling collapses and SECRET_NESTEDQUOTE_1111 is revealed\n"
    )
    out = cb.strip_gm_content(body)
    assert "SECRET_NESTEDQUOTE_1111" not in out["player_body"]
    assert "SECRET_NESTEDQUOTE_1111" not in _mysteries_text(out)


def test_leak_repro_21_nested_question_inside_danger_leaks_into_mysteries():
    body = (
        "> [!danger] hidden\n"
        "> > [!question] a leading question\n"
        "> > who really pulls the strings, SECRET_NESTEDQ_2222\n"
    )
    out = cb.strip_gm_content(body)
    assert "SECRET_NESTEDQ_2222" not in _mysteries_text(out)
    assert "SECRET_NESTEDQ_2222" not in out["player_body"]


def test_leak_repro_22_nested_abstract_inside_danger_leaks_into_recap_seed():
    body = (
        "> [!danger] hidden\n"
        "> > [!abstract] fake recap\n"
        "> > the truth is SECRET_NESTEDABS_3333\n"
    )
    out = cb.strip_gm_content(body)
    assert out["recap_seed"] is None or "SECRET_NESTEDABS_3333" not in out["recap_seed"]
    assert "SECRET_NESTEDABS_3333" not in out["player_body"]


def test_leak_repro_23_nested_example_inside_info_leaks_into_player_body():
    body = (
        "> [!info] lore\n"
        "> > [!example] handout\n"
        "> > the vault code is SECRET_NESTEDEX_4444\n"
    )
    out = cb.strip_gm_content(body)
    assert "SECRET_NESTEDEX_4444" not in out["player_body"]
    assert "SECRET_NESTEDEX_4444" not in _mysteries_text(out)


def test_top_level_quote_example_check_still_kept_and_harvested():
    # Non-regression for the depth-1-only fix: a GENUINE top-level (depth-1)
    # [!quote]/[!example] must still be kept verbatim, and a top-level
    # [!check] must still be harvested into mysteries.
    body = (
        "> [!quote] Recruit\n"
        "> Join us, stranger.\n"
        "> [!example] Handout\n"
        "> A torn map fragment.\n"
        "> [!check] Clue\n"
        "> a torn note mentions the vault.\n"
    )
    out = cb.strip_gm_content(body)
    assert "> [!quote] Recruit" in out["player_body"]
    assert "Join us, stranger." in out["player_body"]
    assert "> [!example] Handout" in out["player_body"]
    assert "A torn map fragment." in out["player_body"]
    assert "[!check]" not in out["player_body"]
    fact = next(m for m in out["mysteries"] if m["kind"] == "fact")
    assert "a torn note mentions the vault." in fact["text"]


# ---------------------------------------------------------------------------
# PR0 task 04 (last nesting leak): a depth-1 keep/harvest block's CONTINUATION
# lines are absorbed regardless of their own depth, so a depth-2+ line riding
# along inside an otherwise-legitimate depth-1 [!quote]/[!check]/[!abstract]
# block leaks verbatim (kept), harvested, or seeded. Fix: only depth-1
# continuation lines belong to the block's kept/harvested content; a depth-2+
# line is dropped (never emitted/harvested/seeded), but does not end the
# block - later depth-1 lines still belong to it.
# ---------------------------------------------------------------------------

def test_leak_repro_24_deeper_continuation_in_quote_leaks_into_player_body():
    body = (
        "> [!quote] Read when sprung\n"
        "> > The trap mechanism SECRET_TRAPCODE_9999 triggers when touched\n"
    )
    out = cb.strip_gm_content(body)
    assert "SECRET_TRAPCODE_9999" not in out["player_body"]
    assert "Read when sprung" in out["player_body"]


def test_leak_repro_25_deeper_continuation_in_check_leaks_into_mysteries():
    body = (
        "> [!check] Confirmed\n"
        "> the fact is CONFIRMED_FACT_1234\n"
        "> > the real answer is SECRET_CHECK_CONT_1234\n"
    )
    out = cb.strip_gm_content(body)
    assert "SECRET_CHECK_CONT_1234" not in _mysteries_text(out)
    assert "SECRET_CHECK_CONT_1234" not in out["player_body"]
    fact = next(m for m in out["mysteries"] if m["kind"] == "fact")
    assert "CONFIRMED_FACT_1234" in fact["text"]


def test_leak_repro_26_deeper_continuation_in_abstract_leaks_into_recap_seed():
    body = (
        "> [!abstract] Recap\n"
        "> the party learned ABSTRACT_FACT_5678\n"
        "> > the true culprit is SECRET_ABSTRACT_CONT_5678\n"
    )
    out = cb.strip_gm_content(body)
    assert out["recap_seed"] is not None
    assert "SECRET_ABSTRACT_CONT_5678" not in out["recap_seed"]
    assert "SECRET_ABSTRACT_CONT_5678" not in out["player_body"]
    assert "ABSTRACT_FACT_5678" in out["recap_seed"]


def test_deeper_continuation_dropped_without_prematurely_ending_the_block():
    # Mixed depths within a single [!quote] block: a depth-2 secret line
    # sandwiched between two depth-1 lines must be dropped WITHOUT ending
    # the block early - both depth-1 lines on either side still belong to
    # it and are kept.
    body = (
        "> [!quote] Mixed\n"
        "> keep this MIXED_KEEP_1\n"
        "> > SECRET drop this MIXED_SECRET_1\n"
        "> keep this too MIXED_KEEP_2\n"
    )
    out = cb.strip_gm_content(body)
    assert "MIXED_KEEP_1" in out["player_body"]
    assert "MIXED_KEEP_2" in out["player_body"]
    assert "MIXED_SECRET_1" not in out["player_body"]


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


def test_resolve_wikilinks_published_unpublished_and_embeds():
    title_to_slug = {
        "Romi Bracken": "romi-bracken",
        "map.png": "assets/map.png",
    }
    body = (
        "You meet [[Romi Bracken]] at the door.\n"
        "She serves [[The Hidden Patron|a shadowy master]].\n"
        "See [[Romi Bracken|the recruiter]] again.\n"
        "![[map.png]]\n"
        "![[secret-gm-diagram.png]]\n"
    )
    out = cb.resolve_wikilinks(body, title_to_slug)

    # Published title -> link, display defaults to the title.
    assert "[Romi Bracken](/chronicle/page/romi-bracken)" in out
    # Aliased published title -> link with the alias as display.
    assert "[the recruiter](/chronicle/page/romi-bracken)" in out
    # Unpublished target -> plain display text, NO link syntax, NO raw wikilink.
    assert "a shadowy master" in out
    assert "The Hidden Patron" not in out
    assert "/chronicle/page/the-hidden-patron" not in out
    assert "[[" not in out
    # Copied asset embed -> markdown image.
    assert "![map.png](assets/map.png)" in out
    # Un-copied asset embed -> stripped entirely.
    assert "secret-gm-diagram" not in out


def test_resolve_wikilinks_unpublished_no_alias_uses_target_as_display():
    out = cb.resolve_wikilinks("Ask [[Unknown Contact]] about it.", {})
    assert out == "Ask Unknown Contact about it."
    assert "[[" not in out and "]]" not in out


def test_resolve_wikilinks_leaves_plain_text_and_non_wikilink_brackets_alone():
    title_to_slug = {"Romi Bracken": "romi-bracken"}
    body = "No links here, just [a footnote-looking thing] and text."
    out = cb.resolve_wikilinks(body, title_to_slug)
    assert out == body


def test_resolve_wikilinks_empty_title_to_slug_degrades_everything():
    body = "[[Romi Bracken]] and [[Alzira|the smuggler]] and ![[map.png]]"
    out = cb.resolve_wikilinks(body, {})
    assert out == "Romi Bracken and the smuggler and "


def test_resolve_wikilinks_heading_anchor_published_no_alias_uses_base_title():
    title_to_slug = {"Romi Bracken": "romi-bracken"}
    out = cb.resolve_wikilinks("See [[Romi Bracken#Motivations]] for more.", title_to_slug)
    assert "[Romi Bracken](/chronicle/page/romi-bracken)" in out
    assert "#Motivations" not in out
    assert "[[" not in out and "]]" not in out


def test_resolve_wikilinks_heading_anchor_published_with_alias():
    title_to_slug = {"Romi Bracken": "romi-bracken"}
    out = cb.resolve_wikilinks(
        "See [[Romi Bracken#Motivations|the recruiter]] for more.", title_to_slug
    )
    assert "[the recruiter](/chronicle/page/romi-bracken)" in out
    assert "#Motivations" not in out
    assert "[[" not in out and "]]" not in out


def test_resolve_wikilinks_block_ref_published_uses_base_title():
    title_to_slug = {"Romi Bracken": "romi-bracken"}
    out = cb.resolve_wikilinks("See [[Romi Bracken^abc123]] for more.", title_to_slug)
    assert "[Romi Bracken](/chronicle/page/romi-bracken)" in out
    assert "^abc123" not in out
    assert "[[" not in out and "]]" not in out


def test_resolve_wikilinks_heading_anchor_unpublished_degrades_to_base_title():
    out = cb.resolve_wikilinks("See [[Hidden Lair#Vault]] for more.", {})
    assert out == "See Hidden Lair for more."
    assert "#Vault" not in out
    assert "[[" not in out and "]]" not in out


def test_build_backlinks_two_page_cross_link():
    pages = [
        {"slug": "romi-bracken", "title": "Romi Bracken",
         "body": "Leader at [C2 Intake](/chronicle/page/c2-intake)."},
        {"slug": "c2-intake", "title": "C2 Intake",
         "body": "Watched over by [Romi](/chronicle/page/romi-bracken). "
                 "See also [Romi again](/chronicle/page/romi-bracken)."},
    ]
    back = cb.build_backlinks(pages)

    # c2-intake is linked from romi-bracken.
    assert back["c2-intake"] == [{"slug": "romi-bracken", "title": "Romi Bracken"}]
    # romi-bracken is linked from c2-intake, deduped despite two references.
    assert back["romi-bracken"] == [{"slug": "c2-intake", "title": "C2 Intake"}]


def test_build_backlinks_ignores_self_and_unknown_targets():
    pages = [
        {"slug": "loop", "title": "Loop",
         "body": "self [x](/chronicle/page/loop) and [ghost](/chronicle/page/nope)."},
    ]
    back = cb.build_backlinks(pages)
    assert back == {"loop": []}


def test_build_manifest_shape_and_validation():
    pages = [
        {"slug": "romi-bracken", "section": "cast", "title": "Romi Bracken",
         "source": "content/romi-bracken.md", "recipients": "all",
         "epithet": "The Recruiter", "tags": ["cult"],
         "session_introduced": 4, "portrait": "assets/romi.png",
         "backlinks": [{"slug": "c2-intake", "title": "C2 Intake"}]},
        {"slug": "c2-intake", "section": "atlas", "title": "C2 Intake",
         "source": "content/c2-intake.md", "recipients": ["kyle"]},
    ]
    manifest = cb.build_manifest(
        campaign_id="shades-of-blood", session_number=5,
        pages=pages, mysteries=[{"kind": "fact", "text": "known"}],
        spine=[{"session": 4}, {"session": 5}], calendar={"era": "AR"},
    )

    assert manifest["schema_version"] == 1
    assert manifest["campaign_id"] == "shades-of-blood"
    assert manifest["session_number"] == 5
    assert manifest["calendar"] == {"era": "AR"}
    assert manifest["fieldguide"] == []
    assert manifest["spine"] == [{"session": 4}, {"session": 5}]
    assert manifest["mysteries"] == [{"kind": "fact", "text": "known"}]
    # generated_at is an ISO-8601 Z timestamp.
    assert manifest["generated_at"].endswith("Z") and "T" in manifest["generated_at"]

    allowed = {"home", "recap", "cast", "atlas", "lore", "handout", "fieldguide"}
    for pg in manifest["pages"]:
        assert SLUG_RE.match(pg["slug"])
        assert pg["section"] in allowed
        assert set(("slug", "section", "title", "source", "recipients")) <= set(pg)

    # Optional fields present when supplied, absent when not.
    romi = next(p for p in manifest["pages"] if p["slug"] == "romi-bracken")
    assert romi["epithet"] == "The Recruiter"
    assert romi["backlinks"] == [{"slug": "c2-intake", "title": "C2 Intake"}]
    c2 = next(p for p in manifest["pages"] if p["slug"] == "c2-intake")
    assert "epithet" not in c2 and "portrait" not in c2
    assert c2["recipients"] == ["kyle"]


def test_build_manifest_rejects_bad_slug_and_section():
    with pytest.raises(ValueError):
        cb.build_manifest("c", 1, [{"slug": "Bad Slug", "section": "cast",
                                    "title": "x", "recipients": "all"}], [], [], {})
    with pytest.raises(ValueError):
        cb.build_manifest("c", 1, [{"slug": "ok", "section": "spoilers",
                                    "title": "x", "recipients": "all"}], [], [], {})


def test_build_manifest_guards_falsy_source():
    # A page dict carrying an explicit falsy `source` (empty string or None)
    # must still fall back to the per-slug default, exactly like an ABSENT
    # source does -- PR1's _chronicle_validate_manifest 400s on a page whose
    # source is falsy, so build_manifest must never let one through.
    pages = [
        {"slug": "empty-source", "section": "lore", "title": "T1",
         "source": "", "recipients": "all"},
        {"slug": "none-source", "section": "lore", "title": "T2",
         "source": None, "recipients": "all"},
        {"slug": "absent-source", "section": "lore", "title": "T3",
         "recipients": "all"},
    ]
    manifest = cb.build_manifest("c", 1, pages, [], [], {})

    for pg in manifest["pages"]:
        assert pg["source"], pg  # every emitted page has a non-empty source

    empty = next(p for p in manifest["pages"] if p["slug"] == "empty-source")
    none_ = next(p for p in manifest["pages"] if p["slug"] == "none-source")
    absent = next(p for p in manifest["pages"] if p["slug"] == "absent-source")
    assert empty["source"] == "content/empty-source.md"
    assert none_["source"] == "content/none-source.md"
    assert absent["source"] == "content/absent-source.md"


def _write_png(path, size_bytes=0):
    # Minimal 1x1 PNG; pad with a trailing filler chunk to hit a target size.
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000d4944415478da6360000002000100057b8fe30000000049454e44ae426082"
    )
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(png)
        if size_bytes:
            f.write(b"\x00" * size_bytes)


def test_collect_assets_copies_referenced_skips_unreferenced(tmp_path):
    vault = tmp_path / "vault"
    portraits = vault / "Player Handouts" / "NPC Portraits"
    _write_png(portraits / "romi.png")
    _write_png(portraits / "unused.png")

    out_assets = tmp_path / "out" / "assets"
    pages = [{"slug": "romi", "body": "Portrait: ![Romi](assets/romi.png)"}]

    copied = cb.collect_assets(pages, str(vault), str(out_assets))

    assert copied == ["romi.png"]
    assert (out_assets / "romi.png").exists()
    assert not (out_assets / "unused.png").exists()


def test_collect_assets_reads_embed_and_portrait_field(tmp_path):
    vault = tmp_path / "vault"
    maps = vault / "Player Handouts" / "Maps"
    _write_png(maps / "intake.png")
    portraits = vault / "Player Handouts" / "NPC Portraits"
    _write_png(portraits / "alzira.png")

    out_assets = tmp_path / "out" / "assets"
    pages = [
        {"slug": "intake", "body": "![[Maps/intake.png]]"},
        {"slug": "alzira", "portrait": "NPC Portraits/alzira.png", "body": ""},
    ]

    copied = cb.collect_assets(pages, str(vault), str(out_assets))

    assert copied == ["alzira.png", "intake.png"]


def test_collect_assets_pillow_absent_still_copies(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _write_png(vault / "Player Handouts" / "NPC Portraits" / "romi.png")
    out_assets = tmp_path / "out" / "assets"
    pages = [{"slug": "romi", "body": "![[romi.png]]"}]

    # Force `from PIL import Image` to raise ImportError.
    monkeypatch.setitem(sys.modules, "PIL", None)

    copied = cb.collect_assets(pages, str(vault), str(out_assets))

    assert copied == ["romi.png"]
    assert (out_assets / "romi.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_collect_assets_skips_oversize_over_budget(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _write_png(vault / "Player Handouts" / "Maps" / "big.png", size_bytes=1024)
    out_assets = tmp_path / "out" / "assets"
    pages = [{"slug": "big", "body": "![[big.png]]"}]

    monkeypatch.setattr(cb, "ASSET_BUDGET_BYTES", 100)  # smaller than the file
    copied = cb.collect_assets(pages, str(vault), str(out_assets))

    assert copied == []
    assert not (out_assets / "big.png").exists()


def test_collect_assets_warns_on_basename_collision_different_files(tmp_path, caplog):
    # Two DIFFERENT source images share a basename across subfolders (the
    # gap this test guards): the second one used to be dropped via a bare
    # `continue` in the dedup branch with zero signal. It must now log a
    # warning instead of vanishing silently.
    vault = tmp_path / "vault"
    _write_png(vault / "Player Handouts" / "Maps" / "cover.png")
    _write_png(vault / "Player Handouts" / "NPC Portraits" / "cover.png", size_bytes=64)

    out_assets = tmp_path / "out" / "assets"
    pages = [
        {"slug": "map-page", "body": "![[Maps/cover.png]]"},
        {"slug": "npc-page", "body": "![[NPC Portraits/cover.png]]"},
    ]

    with caplog.at_level(logging.WARNING, logger="chronicle_build"):
        copied = cb.collect_assets(pages, str(vault), str(out_assets))

    # Basename dedup / copy behavior is unchanged: only the first-seen file
    # is copied, under its basename.
    assert copied == ["cover.png"]

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("cover.png" in w and "collision" in w.lower() for w in warnings), warnings


def test_collect_assets_same_basename_same_file_stays_silent(tmp_path, caplog):
    # A genuine duplicate reference (portrait field + embed both pointing
    # at the same actual file) is NOT a collision - no warning.
    vault = tmp_path / "vault"
    _write_png(vault / "Player Handouts" / "NPC Portraits" / "romi.png")

    out_assets = tmp_path / "out" / "assets"
    pages = [
        {"slug": "romi-a", "body": "![[NPC Portraits/romi.png]]"},
        {"slug": "romi-b", "portrait": "romi.png", "body": ""},
    ]

    with caplog.at_level(logging.WARNING, logger="chronicle_build"):
        copied = cb.collect_assets(pages, str(vault), str(out_assets))

    assert copied == ["romi.png"]
    collision_warnings = [r for r in caplog.records if "collision" in r.getMessage().lower()]
    assert collision_warnings == []


def test_collect_assets_real_pillow_roundtrip(tmp_path):
    # The `_write_png` fixture's 1x1 PNG doesn't decode under real Pillow,
    # so the other collect_assets tests only ever hit the copy-as-is
    # fallback branch of `_strip_exif`. This test uses a Pillow-generated,
    # genuinely decodable PNG so the real `Image.open`/`putdata`/`save`
    # round-trip actually runs.
    pytest.importorskip("PIL")
    from PIL import Image

    vault = tmp_path / "vault"
    portraits = vault / "Player Handouts" / "NPC Portraits"
    portraits.mkdir(parents=True)
    src_path = portraits / "romi.png"
    Image.new("RGB", (4, 4), color=(10, 20, 30)).save(src_path)

    out_assets = tmp_path / "out" / "assets"
    pages = [{"slug": "romi", "body": "![[romi.png]]"}]

    copied = cb.collect_assets(pages, str(vault), str(out_assets))

    assert copied == ["romi.png"]
    out_path = out_assets / "romi.png"
    assert out_path.exists()
    with Image.open(out_path) as img:
        img.load()
        assert img.size == (4, 4)


# --- leak_check -------------------------------------------------------------


def test_leak_check_clean_tree_returns_empty(tmp_path):
    out = tmp_path / "out"
    (out / "content").mkdir(parents=True)
    (out / "content" / "romi.md").write_text(
        "> [!quote] Read aloud\n> The door opens.\n", encoding="utf-8")
    (out / "manifest.json").write_text('{"schema_version": 1, "pages": []}', encoding="utf-8")

    assert cb.leak_check(str(out)) == []


def test_leak_check_catches_planted_danger(tmp_path):
    out = tmp_path / "out"
    (out / "content").mkdir(parents=True)
    (out / "content" / "romi.md").write_text(
        "Intro text\n\n> [!danger] Romi is the cult leader\n> secret motive\n", encoding="utf-8")
    (out / "manifest.json").write_text('{"schema_version": 1, "pages": []}', encoding="utf-8")

    offenders = cb.leak_check(str(out))
    assert offenders == ["content/romi.md: [!danger]"]


def test_leak_check_catches_secret_and_gm_including_manifest(tmp_path):
    out = tmp_path / "out"
    (out / "content").mkdir(parents=True)
    (out / "content" / "a.md").write_text("> [!secret] hidden\n", encoding="utf-8")
    (out / "manifest.json").write_text('{"note": "[!gm] leaked into manifest"}', encoding="utf-8")

    offenders = cb.leak_check(str(out))
    assert "content/a.md: [!secret]" in offenders
    assert "manifest.json: [!gm]" in offenders


def test_leak_check_case_and_whitespace_tolerant():
    # Matches the PR1 app's ingest re-scan (_chronicle_leak_scan): the
    # marker regex must be case-insensitive and tolerate stray whitespace
    # inside the brackets, so nothing that would 400 at ingest slips
    # through the build-time gate first.
    assert cb._LEAK_RE.search("[!DANGER] shout-cased marker")
    assert cb._LEAK_RE.search("[! gm ] padded marker")
    assert cb._LEAK_RE.search("[!Secret] mixed case")


def test_leak_check_scans_nested_subdirectories(tmp_path):
    out = tmp_path / "out"
    (out / "content" / "cast").mkdir(parents=True)
    (out / "content" / "cast" / "romi.md").write_text(
        "> [! Danger ] nested and padded\n", encoding="utf-8")
    (out / "manifest.json").write_text("{}", encoding="utf-8")

    offenders = cb.leak_check(str(out))
    assert offenders == ["content/cast/romi.md: [!danger]"]


def test_leak_check_ignores_non_md_non_manifest_files(tmp_path):
    out = tmp_path / "out"
    (out / "assets").mkdir(parents=True)
    # A binary/other asset that happens to contain the marker bytes must
    # not be scanned -- only .md content and manifest.json are in scope.
    (out / "assets" / "notes.txt").write_text("[!danger] not a real page\n", encoding="utf-8")

    assert cb.leak_check(str(out)) == []


def test_leak_check_scans_files_with_stray_non_utf8_bytes(tmp_path):
    # A file with a genuine [!danger] callout plus a stray non-UTF-8 byte
    # must still be caught. A strict utf-8 decode raises UnicodeDecodeError
    # on the whole file, which the old code swallowed and skipped entirely
    # -- silently passing a real spoiler leak. PR1's ingest re-scan
    # (_chronicle_leak_scan) opens with errors='ignore', so this firewall
    # backstop must be at least as strong: skip only the bad byte, not the
    # whole file.
    out = tmp_path / "out"
    (out / "content").mkdir(parents=True)
    path = out / "content" / "romi.md"
    path.write_bytes(
        b"Intro text\n\n> [!danger] Romi is the cult leader\n> secret mo\xfftive\n"
    )
    (out / "manifest.json").write_text('{"schema_version": 1, "pages": []}', encoding="utf-8")

    offenders = cb.leak_check(str(out))
    assert offenders == ["content/romi.md: [!danger]"]


def test_leak_check_subpaths_scopes_to_managed_outputs(tmp_path):
    # M4: with `subpaths`, only the tool's own managed outputs are scanned;
    # a hand-authored note the GM keeps alongside them (Option A) is ignored,
    # so an in-world [!danger] there does not false-positive-abort the build.
    out = tmp_path / "out"
    (out / "content").mkdir(parents=True)
    (out / "content" / "romi.md").write_text("# Romi\nA quiet clerk.\n", encoding="utf-8")
    (out / "manifest.json").write_text('{"schema_version": 1}', encoding="utf-8")
    (out / "01 - Chronicle").mkdir(parents=True)
    (out / "01 - Chronicle" / "Home.md").write_text(
        "> [!danger] The bridge is unstable.\n", encoding="utf-8")

    # Whole-tree scan still flags the hand-authored note...
    assert cb.leak_check(str(out)) == ["01 - Chronicle/Home.md: [!danger]"]
    # ...but the scoped scan sees only manifest.json + content/ -> clean.
    assert cb.leak_check(str(out), subpaths=("manifest.json", "content")) == []


def test_leak_check_subpaths_still_catches_managed_leak(tmp_path):
    out = tmp_path / "out"
    (out / "content").mkdir(parents=True)
    (out / "content" / "boom.md").write_text(
        "> [!secret] the cult meets below\n", encoding="utf-8")
    (out / "manifest.json").write_text('{"schema_version": 1}', encoding="utf-8")

    assert cb.leak_check(str(out), subpaths=("manifest.json", "content")) == \
        ["content/boom.md: [!secret]"]


def test_leak_check_subpaths_absent_entries_are_clean(tmp_path):
    # An entirely absent --out (build_player_vault never wrote it, e.g. on a
    # leak) yields no offenders from the scoped re-scan.
    out = tmp_path / "does-not-exist"
    assert cb.leak_check(str(out), subpaths=("manifest.json", "content")) == []


def test_make_zip_defaults_to_temp_outside_out_dir(tmp_path):
    # M3: with no explicit zip_path, the archive lands OUTSIDE out_dir so the
    # GM's real vault (Option A) never accrues a chronicle.zip artifact.
    out = tmp_path / "out"
    (out / "content").mkdir(parents=True)
    (out / "manifest.json").write_text('{"schema_version": 1}', encoding="utf-8")
    (out / "content" / "romi.md").write_text("# Romi\n", encoding="utf-8")

    zip_path = cb.make_zip(str(out))
    try:
        assert os.path.exists(zip_path)
        assert zip_path.endswith(".zip")
        assert os.path.dirname(zip_path) != str(out)
        assert not (out / "chronicle.zip").exists()
        with zipfile.ZipFile(zip_path) as zf:
            assert "manifest.json" in zf.namelist()
            assert "content/romi.md" in zf.namelist()
    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)


# --- build_player_vault (A3.3 orchestration) --------------------------------


def test_build_player_vault_end_to_end(tmp_path):
    out = tmp_path / "out"
    result = cb.build_player_vault(FIXTURE, str(out), campaign_id="shades-of-blood")

    manifest = result["manifest"]
    assert manifest["schema_version"] == 1
    assert manifest["campaign_id"] == "shades-of-blood"
    assert isinstance(result["review_summary"], str) and result["review_summary"]

    # The encountered NPC (Romi) became a cast page with a safe slug.
    slugs = {p["slug"] for p in manifest["pages"]}
    assert "romi-bracken" in slugs
    for p in manifest["pages"]:
        assert SLUG_RE.match(p["slug"])
        assert p["section"] in {"home", "recap", "cast", "atlas", "lore", "handout", "fieldguide"}

    # Alzira is `chronicle: false` and never encountered - excluded entirely.
    assert "alzira-vane" not in slugs

    # GM content is GONE from every emitted content file.
    content_dir = out / "content"
    joined = "\n".join(p.read_text(encoding="utf-8") for p in sorted(content_dir.iterdir()))
    assert "[!danger]" not in joined
    assert "[!info]" not in joined      # info is GM-only per policy
    assert "cult leader" not in joined.lower()   # planted spoiler string in the fixture danger block
    assert "[!quote]" in joined         # player-facing read-aloud preserved

    # The firewall agrees the tree is clean.
    assert cb.leak_check(str(out)) == []


# ---------------------------------------------------------------------------
# Final-review fix I1: PR1's Home (latest_recap) + the Story So Far timeline
# read recaps from `pages[section=='recap']` -- they never read `spine`.
# build_player_vault must synthesize a `section: recap` page per COMPLETED
# session note (in addition to the existing spine[] entry, which Phase 2
# still uses), or the player-facing UI never shows anything.
# ---------------------------------------------------------------------------


def test_build_player_vault_synthesizes_recap_page_per_completed_session(tmp_path):
    out = tmp_path / "out"
    result = cb.build_player_vault(FIXTURE, str(out), campaign_id="shades-of-blood")
    manifest = result["manifest"]

    recap_pages = [p for p in manifest["pages"] if p["section"] == "recap"]
    assert len(recap_pages) == 1
    recap = recap_pages[0]
    assert recap["slug"] == "session-5"
    assert recap["title"] == "Session 5"
    assert recap["session_updated"] == 5
    assert recap["session_introduced"] == 5
    assert recap["recipients"] == "all"
    assert recap["source"] == "content/session-5.md"

    recap_path = out / "content" / "session-5.md"
    assert recap_path.exists()
    recap_body = recap_path.read_text(encoding="utf-8")
    assert ("The party breached the Intake Entrance and met Romi Bracken "
            "at the glowing door.") in recap_body

    # No GM secret from the same session note leaks into the recap page.
    assert "azlanti" not in recap_body.lower()
    assert "camazotz" not in recap_body.lower()

    # spine[] seeding (Phase 2) is preserved alongside the new recap page.
    assert manifest["spine"]
    assert manifest["spine"][0]["session_number"] == 5


# ---------------------------------------------------------------------------
# Final-review fix (duplicate title): a detail page's body must not repeat
# an H1 that already duplicates the rendered page.title (the template's own
# <h1>). Only a LEADING H1 whose text matches the title (case-insensitive,
# trimmed) is dropped -- any other heading is left alone.
# ---------------------------------------------------------------------------


def test_build_player_vault_dedupes_leading_h1_matching_page_title(tmp_path):
    out = tmp_path / "out"
    cb.build_player_vault(FIXTURE, str(out), campaign_id="shades-of-blood")

    romi_body = (out / "content" / "romi-bracken.md").read_text(encoding="utf-8")
    assert not romi_body.lstrip().lower().startswith("# romi bracken")
    # the rest of the body is untouched
    assert "A warm shopkeeper with an easy smile" in romi_body
    assert "> [!quote] Recruitment Pitch" in romi_body


def test_dedupe_leading_title_heading_matches_and_drops():
    body = "# Romi Bracken\n\nA warm shopkeeper with an easy smile.\n"
    out = cb._dedupe_leading_title_heading(body, "Romi Bracken")
    assert not out.lstrip().lower().startswith("# romi bracken")
    assert "A warm shopkeeper with an easy smile." in out


def test_dedupe_leading_title_heading_case_and_whitespace_insensitive():
    body = "#   ROMI bracken   \n\nBody text.\n"
    out = cb._dedupe_leading_title_heading(body, "Romi Bracken")
    assert "#" not in out.split("\n")[0]
    assert "Body text." in out


def test_dedupe_leading_title_heading_leaves_different_heading_untouched():
    body = "# A Different Heading\n\nBody text.\n"
    out = cb._dedupe_leading_title_heading(body, "Romi Bracken")
    assert out == body


def test_dedupe_leading_title_heading_leaves_non_h1_first_line_untouched():
    body = "Just prose, no heading at all.\n"
    out = cb._dedupe_leading_title_heading(body, "Romi Bracken")
    assert out == body


def test_dedupe_leading_title_heading_only_touches_leading_h1():
    # A LATER heading matching the title, after other content, must not be
    # touched -- only a leading H1 is ever a candidate.
    body = "Some intro text.\n\n# Romi Bracken\n\nMore text.\n"
    out = cb._dedupe_leading_title_heading(body, "Romi Bracken")
    assert out == body


def test_build_player_vault_harvests_mysteries(tmp_path):
    out = tmp_path / "out"
    result = cb.build_player_vault(FIXTURE, str(out), campaign_id="shades-of-blood")
    kinds = {m["kind"] for m in result["manifest"]["mysteries"]}
    assert "fact" in kinds        # from [!check]
    assert "question" in kinds    # from [!question]


def test_build_player_vault_review_summary_reports_unmatched_and_slugs(tmp_path):
    # Carry-forward requirement: an encountered entity (or override) with no
    # findable note must WARN in review_summary, not vanish silently (Task
    # 5's exact-string-match gap). The fixture's Session 5 encounters "Cult
    # Patrol Guards" (no NPC note anywhere in the vault) and covers areas
    # C2/C3/C11 (only C2 has a Location note).
    out = tmp_path / "out"
    result = cb.build_player_vault(FIXTURE, str(out), campaign_id="shades-of-blood")
    summary = result["review_summary"].lower()

    assert "cult patrol guards" in summary
    assert "c3" in summary
    assert "c11" in summary
    # Published slugs are surfaced too, not just per-section titles.
    assert "romi-bracken" in summary


def test_build_player_vault_combined_map_resolves_links_and_asset_embeds(tmp_path):
    # Carry-forward requirement: ONE combined map (page title -> slug AND
    # asset ref -> published asset path) is passed to resolve_wikilinks, and
    # the asset paths that land in content/manifest must match what
    # collect_assets actually wrote to out/assets - not the raw vault-relative
    # ref the GM happened to type.
    vault = tmp_path / "vault"
    _write_png(vault / "Player Handouts" / "NPC Portraits" / "romi.png")
    _write_vault_note(vault / "NPCs" / "Romi Bracken.md", """\
        ---
        type: npc
        name: Romi Bracken
        chronicle: true
        portrait: NPC Portraits/romi.png
        ---
        ![[NPC Portraits/romi.png]]

        Romi works with [[Alzira Vane]].
        """)
    _write_vault_note(vault / "NPCs" / "Alzira Vane.md", """\
        ---
        type: npc
        name: Alzira Vane
        chronicle: true
        ---
        A quiet contact.
        """)

    out = tmp_path / "out"
    result = cb.build_player_vault(str(vault), str(out), campaign_id="c")

    romi = next(p for p in result["manifest"]["pages"] if p["slug"] == "romi-bracken")
    # The portrait field is rewritten to the path collect_assets actually
    # wrote to (assets/<basename>), not the raw "NPC Portraits/romi.png" ref.
    assert romi["portrait"] == "assets/romi.png"
    assert (out / "assets" / "romi.png").exists()

    romi_body = (out / "content" / "romi-bracken.md").read_text(encoding="utf-8")
    assert "![NPC Portraits/romi.png](assets/romi.png)" in romi_body
    assert "[Alzira Vane](/chronicle/page/alzira-vane)" in romi_body

    assert cb.leak_check(str(out)) == []


def test_build_player_vault_end_to_end_secrets_absent_from_every_file(tmp_path):
    # Task 12 hardening: assert the fixture's ACTUAL planted secret strings
    # (not just the trivially-true `role:` frontmatter field) are absent from
    # every emitted content file AND manifest.json, case-insensitively.
    out = tmp_path / "out"
    cb.build_player_vault(FIXTURE, str(out), campaign_id="shades-of-blood")

    content_dir = out / "content"
    joined = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(content_dir.iterdir())
    ).lower()
    manifest_text = (out / "manifest.json").read_text(encoding="utf-8").lower()

    for secret in ("camazotz", "sacrifice", "azlanti tech"):
        assert secret not in joined, secret
        assert secret not in manifest_text, secret


def test_build_player_vault_warns_on_missing_asset(tmp_path):
    # Important 2: a page references a portrait/embed with no matching file
    # under Player Handouts -> review_summary must surface an asset warning
    # (the not-found/collision surfacing collect_assets already logs, but
    # which build_player_vault's own tests never exercised end-to-end).
    vault = tmp_path / "vault"
    _write_vault_note(vault / "NPCs" / "Ghost.md", """\
        ---
        type: npc
        name: Ghost
        chronicle: true
        portrait: NPC Portraits/ghost.png
        ---
        ![[NPC Portraits/ghost.png]]

        A pale figure, never actually pictured.
        """)
    out = tmp_path / "out"
    result = cb.build_player_vault(str(vault), str(out), campaign_id="c")

    summary = result["review_summary"].lower()
    assert "ghost.png" in summary
    assert "not found" in summary


def test_build_player_vault_skips_underscore_and_reference_meta_files(tmp_path):
    # Minor: `Player Handouts/_README.md` (an underscore-prefixed GM meta file
    # with `type: reference`) must never become a published page.
    out = tmp_path / "out"
    result = cb.build_player_vault(FIXTURE, str(out), campaign_id="shades-of-blood")

    titles = {p["title"] for p in result["manifest"]["pages"]}
    assert not any("secret-free" in t.lower() for t in titles)

    content_names = {p.name.lower() for p in (out / "content").iterdir()}
    assert not any("readme" in n for n in content_names)


def test_build_player_vault_never_touches_out_dir_on_leak(tmp_path, monkeypatch):
    # CRITICAL data-safety regression guard: build_player_vault must NEVER
    # rmtree/modify the caller's out_dir on a leak. It stages the whole
    # build into a private temp dir first; a leak there means the staging
    # dir is discarded and out_dir -- the GM's real persistent Obsidian
    # player vault -- is never touched at all.
    out = tmp_path / "out"
    out.mkdir()
    sentinel = out / "GM_PRECIOUS.txt"
    sentinel.write_text("do not delete", encoding="utf-8")
    prior_manifest = out / "manifest.json"
    prior_manifest.write_text('{"prior": true}', encoding="utf-8")

    # Simulate a firewall bypass (e.g. a future strip_gm_content regression):
    # the per-note strip becomes a no-op, so a `[!danger]` block rides
    # straight through into content/*.md. leak_check is the second,
    # independent layer - build_player_vault must treat ANY surviving marker
    # as fatal, but must NEVER raise or touch out_dir: it returns the
    # offenders in `result["leaks"]` for the CLI to act on.
    monkeypatch.setattr(cb, "strip_gm_content", lambda body: {
        "player_body": body, "mysteries": [], "recap_seed": None,
    })

    result = cb.build_player_vault(FIXTURE, str(out), campaign_id="shades-of-blood")

    assert result["leaks"]  # non-empty: the forced leak was detected
    assert "leak" in result["review_summary"].lower()

    # out_dir was NOT touched: the sentinel and prior manifest survive
    # untouched, and no content/assets subtree was written into it.
    assert sentinel.exists()
    assert sentinel.read_text(encoding="utf-8") == "do not delete"
    assert prior_manifest.read_text(encoding="utf-8") == '{"prior": true}'
    assert not (out / "content").exists()
    assert not (out / "assets").exists()


def test_build_player_vault_clean_build_reports_empty_leaks(tmp_path):
    out = tmp_path / "out"
    result = cb.build_player_vault(FIXTURE, str(out), campaign_id="shades-of-blood")
    assert result["leaks"] == []


def test_build_player_vault_clean_build_preserves_unrelated_files(tmp_path):
    # Data-safety regression guard (Task 12 review) for the CLEAN-build path
    # (mirrors test_build_player_vault_never_touches_out_dir_on_leak, which
    # covers the leak path). On a clean build, build_player_vault must:
    #   - preserve unrelated pre-existing content already in out_dir (the
    #     GM's real Obsidian vault may hold `.obsidian/` config and other
    #     files that have nothing to do with the managed build outputs);
    #   - still REPLACE the managed `content/` subtree wholesale, so a stale
    #     page from a previous build doesn't linger forever.
    out = tmp_path / "out"
    obsidian_dir = out / ".obsidian"
    obsidian_dir.mkdir(parents=True)
    workspace_json = obsidian_dir / "workspace.json"
    workspace_json.write_text('{"main": {"id": "prior-workspace"}}', encoding="utf-8")

    keep_me = out / "GM_KEEP_ME.txt"
    keep_me.write_text("unrelated GM file, do not delete", encoding="utf-8")

    stale_content_dir = out / "content"
    stale_content_dir.mkdir()
    stale_page = stale_content_dir / "old-page.md"
    stale_page.write_text("stale content from a previous build", encoding="utf-8")

    result = cb.build_player_vault(FIXTURE, str(out), campaign_id="sample")

    assert result["leaks"] == []

    # Unrelated pre-existing out_dir content survives untouched.
    assert workspace_json.exists()
    assert workspace_json.read_text(encoding="utf-8") == '{"main": {"id": "prior-workspace"}}'
    assert keep_me.exists()
    assert keep_me.read_text(encoding="utf-8") == "unrelated GM file, do not delete"

    # The stale prior page is gone: content/ was replaced, not merged into.
    assert not stale_page.exists()

    # The fresh build actually landed: a real page plus manifest.json.
    assert (out / "content" / "romi-bracken.md").exists()
    assert (out / "manifest.json").exists()


def test_make_zip_has_manifest_at_root_and_skips_gitkeep(tmp_path):
    out = tmp_path / "out"
    (out / "content").mkdir(parents=True)
    (out / "assets").mkdir(parents=True)
    (out / "manifest.json").write_text('{"schema_version": 1}', encoding="utf-8")
    (out / "content" / "romi.md").write_text("# Romi\n", encoding="utf-8")
    (out / "content" / ".gitkeep").write_text("", encoding="utf-8")
    (out / "assets" / "romi.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    zip_path = cb.make_zip(str(out))

    assert os.path.exists(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "content/romi.md" in names
        assert "assets/romi.png" in names
        assert not any(n.endswith(".gitkeep") for n in names)
        assert zf.read("manifest.json") == b'{"schema_version": 1}'


# --- publish (multipart POST via urllib, no new dep) ------------------------


class _FakeResp:
    def __init__(self, status=200, body=b'{"ok": true}'):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_publish_posts_multipart_with_token(tmp_path, monkeypatch):
    zip_path = tmp_path / "chronicle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", "{}")

    captured = {}

    def fake_urlopen(req, *a, **kw):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["content_type"] = req.get_header("Content-type")
        captured["token"] = req.get_header("X-chronicle-token")
        captured["data"] = req.data
        return _FakeResp()

    monkeypatch.setattr(cb.urllib.request, "urlopen", fake_urlopen)

    ok, resp = cb.publish(str(zip_path), "https://tableview.up.railway.app/api/chronicle/publish",
                          token="sekret")

    assert ok is True
    assert resp == '{"ok": true}'
    assert captured["method"] == "POST"
    assert captured["content_type"].startswith("multipart/form-data; boundary=")
    assert captured["token"] == "sekret"
    assert b'name="archive"' in captured["data"]
    assert b"PK" in captured["data"]  # the zip bytes are in the body


def test_publish_no_token_omits_header_and_reports_http_error(tmp_path, monkeypatch):
    zip_path = tmp_path / "chronicle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", "{}")

    def fake_urlopen(req, *a, **kw):
        assert req.get_header("X-chronicle-token") is None
        raise cb.urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {}, io.BytesIO(b"leak detected"))

    monkeypatch.setattr(cb.urllib.request, "urlopen", fake_urlopen)

    ok, resp = cb.publish(str(zip_path), "https://example/api/chronicle/publish")
    assert ok is False
    assert "leak detected" in resp


# --- main / CLI --------------------------------------------------------------


def test_main_dry_run_prints_summary_and_does_not_publish(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"

    def boom(*a, **k):
        raise AssertionError("publish must not be called on --dry-run")
    monkeypatch.setattr(cb, "publish", boom)

    zipped = {"called": False}
    real_make_zip = cb.make_zip
    monkeypatch.setattr(cb, "make_zip", lambda d: zipped.__setitem__("called", True) or real_make_zip(d))

    rc = cb.main([
        "--vault", str(FIXTURE), "--out", str(out),
        "--campaign-id", "shades-of-blood",
        "--publish-url", "https://example/api/chronicle/publish",
        "--dry-run",
    ])

    assert rc == 0
    assert zipped["called"] is False
    printed = capsys.readouterr().out
    assert "Pages:" in printed          # review summary reached stdout
    assert not (out / "chronicle.zip").exists()


def test_main_aborts_nonzero_on_leak_and_never_zips(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"

    def leaky_build(vault_dir, out_dir, campaign_id):
        os.makedirs(os.path.join(out_dir, "content"), exist_ok=True)
        with open(os.path.join(out_dir, "content", "boom.md"), "w", encoding="utf-8") as f:
            f.write("> [!danger] planted spoiler survived\n")
        with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
            f.write('{"schema_version": 1, "pages": []}')
        return {"manifest": {"schema_version": 1}, "review_summary": "Pages: 1"}

    monkeypatch.setattr(cb, "build_player_vault", leaky_build)
    monkeypatch.setattr(cb, "make_zip", lambda d: (_ for _ in ()).throw(
        AssertionError("make_zip must not run when a leak is present")))
    monkeypatch.setattr(cb, "publish", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("publish must not run when a leak is present")))

    rc = cb.main(["--vault", str(tmp_path), "--out", str(out), "--campaign-id", "x"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "LEAK CHECK FAILED" in err
    assert "content/boom.md: [!danger]" in err
    assert not (out / "chronicle.zip").exists()


def test_main_aborts_nonzero_when_build_player_vault_reports_leaks(tmp_path, monkeypatch, capsys):
    # Mirrors the Task 12 contract directly: build_player_vault reports a
    # non-empty `leaks` list (and, per its own contract, never touches
    # out_dir on a leak) -- main must still catch this via result["leaks"]
    # even though a `leak_check(args.out)` re-scan of an untouched/absent
    # out_dir would itself find nothing.
    out = tmp_path / "out"

    def leaky_build(vault_dir, out_dir, campaign_id):
        return {"manifest": {"schema_version": 1},
                "review_summary": "!!! BUILD ABORTED - SPOILER LEAK DETECTED !!!\n"
                                   "  content/cult-hideout.md: [!danger]",
                "leaks": ["content/cult-hideout.md: [!danger]"]}

    monkeypatch.setattr(cb, "build_player_vault", leaky_build)
    monkeypatch.setattr(cb, "make_zip", lambda d: (_ for _ in ()).throw(
        AssertionError("make_zip must not run when a leak is present")))
    monkeypatch.setattr(cb, "publish", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("publish must not run when a leak is present")))

    rc = cb.main(["--vault", str(tmp_path), "--out", str(out), "--campaign-id", "x"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "LEAK CHECK FAILED" in err
    assert "content/cult-hideout.md: [!danger]" in err
    assert not out.exists() or not (out / "chronicle.zip").exists()


def test_main_clean_build_publishes_and_returns_zero(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    published = {}

    def fake_publish(zip_path, url, token=None):
        published["zip_path"] = zip_path
        published["url"] = url
        published["token"] = token
        # the archive must exist AT publish time (cleanup happens after)
        published["existed_at_publish"] = os.path.exists(zip_path)
        return True, '{"ok": true}'

    monkeypatch.setattr(cb, "publish", fake_publish)

    rc = cb.main([
        "--vault", str(FIXTURE), "--out", str(out),
        "--campaign-id", "shades-of-blood",
        "--publish-url", "https://example/api/chronicle/publish",
        "--token", "sekret",
    ])

    assert rc == 0
    assert published["url"] == "https://example/api/chronicle/publish"
    assert published["token"] == "sekret"
    assert published["existed_at_publish"] is True
    assert published["zip_path"].endswith(".zip")
    # M3: the archive is a temp file OUTSIDE --out and is cleaned up after.
    assert not (out / "chronicle.zip").exists()
    assert os.path.dirname(published["zip_path"]) != str(out)
    assert not os.path.exists(published["zip_path"])
    printed = capsys.readouterr().out
    assert "Pages:" in printed
    assert "Published:" in printed


def test_main_clean_build_publish_failure_returns_nonzero(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    seen = {}

    def failing_publish(zip_path, url, token=None):
        # make_zip runs before publish is attempted -> the archive exists here
        seen["existed_at_publish"] = os.path.exists(zip_path)
        return (False, "server rejected: leak detected")

    monkeypatch.setattr(cb, "publish", failing_publish)

    rc = cb.main([
        "--vault", str(FIXTURE), "--out", str(out),
        "--campaign-id", "shades-of-blood",
        "--publish-url", "https://example/api/chronicle/publish",
    ])

    assert rc == 1
    err = capsys.readouterr().err
    assert "Publish FAILED" in err
    assert "leak detected" in err
    assert seen["existed_at_publish"] is True
    # M3: no stray archive left behind in --out even on a failed publish.
    assert not (out / "chronicle.zip").exists()


def test_main_clean_build_no_publish_url_zips_and_returns_zero(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"

    def boom(*a, **k):
        raise AssertionError("publish must not be called without --publish-url")
    monkeypatch.setattr(cb, "publish", boom)

    rc = cb.main([
        "--vault", str(FIXTURE), "--out", str(out),
        "--campaign-id", "shades-of-blood",
    ])

    assert rc == 0
    # M3: archive is built to temp and removed; --out stays clean.
    assert not (out / "chronicle.zip").exists()
    printed = capsys.readouterr().out
    assert "Pages:" in printed
    assert "Wrote archive:" in printed


def test_main_rescan_ignores_hand_authored_notes_outside_managed_outputs(tmp_path, monkeypatch, capsys):
    # M4 (Option A): --out is the GM's real player vault holding hand-authored
    # notes alongside the tool's managed manifest.json + content/. A legitimate
    # in-world [!danger] callout in one of THOSE notes must NOT abort the build.
    out = tmp_path / "out"

    def clean_managed_build(vault_dir, out_dir, campaign_id):
        os.makedirs(os.path.join(out_dir, "content"), exist_ok=True)
        with open(os.path.join(out_dir, "content", "romi.md"), "w", encoding="utf-8") as f:
            f.write("# Romi\nA quiet clerk.\n")
        with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
            f.write('{"schema_version": 1, "pages": []}')
        # A hand-authored player note the GM keeps in the same vault folder,
        # using an in-world danger warning -- NOT a managed output.
        os.makedirs(os.path.join(out_dir, "01 - Chronicle"), exist_ok=True)
        with open(os.path.join(out_dir, "01 - Chronicle", "Home.md"), "w", encoding="utf-8") as f:
            f.write("> [!danger] The bridge over the chasm is unstable.\n")
        return {"manifest": {"schema_version": 1}, "review_summary": "Pages: 1", "leaks": []}

    monkeypatch.setattr(cb, "build_player_vault", clean_managed_build)
    monkeypatch.setattr(cb, "publish", lambda *a, **k: (True, '{"ok": true}'))

    rc = cb.main([
        "--vault", str(tmp_path), "--out", str(out), "--campaign-id", "x",
        "--publish-url", "https://example/api/chronicle/publish",
    ])

    assert rc == 0
    err = capsys.readouterr().err
    assert "LEAK CHECK FAILED" not in err


def test_main_rescan_still_catches_leak_in_managed_content(tmp_path, monkeypatch, capsys):
    # The scoped re-scan must still catch a survivor inside content/ even when
    # build_player_vault fails to report it via result["leaks"].
    out = tmp_path / "out"

    def leaky_managed_build(vault_dir, out_dir, campaign_id):
        os.makedirs(os.path.join(out_dir, "content"), exist_ok=True)
        with open(os.path.join(out_dir, "content", "boom.md"), "w", encoding="utf-8") as f:
            f.write("> [!secret] the cult meets under the cathedral\n")
        with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
            f.write('{"schema_version": 1, "pages": []}')
        return {"manifest": {"schema_version": 1}, "review_summary": "Pages: 1"}

    monkeypatch.setattr(cb, "build_player_vault", leaky_managed_build)
    monkeypatch.setattr(cb, "make_zip", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("make_zip must not run when a leak is present")))
    monkeypatch.setattr(cb, "publish", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("publish must not run when a leak is present")))

    rc = cb.main(["--vault", str(tmp_path), "--out", str(out), "--campaign-id", "x"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "LEAK CHECK FAILED" in err
    assert "content/boom.md: [!secret]" in err
