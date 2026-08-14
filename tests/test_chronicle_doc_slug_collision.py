"""A document whose address collides with a vault page must not be a dead end.

Reported from real use: uploading a document produced

    Blocked: a vault page already uses the address d-story-so-far.
    Rename this document to publish it.

The read side DROPS such a document rather than shadowing the vault -- the vault
owns the unprefixed namespace and its pages carry backlinks -- so the document
can never be published while it holds that address. That part is correct.

What was wrong is that the way out did not reliably work. `unique_slug` stepped
around other DOCUMENTS only, so:

  * the new title could land on a different vault page and be blocked again, and
  * retyping the SAME title regenerated the SAME slug and changed nothing,
    which is the obvious thing to try when a screen says "rename this".

Reserving the vault's slugs makes the collision resolve itself.
"""
from __future__ import annotations

from core import chronicle_docs as lib


VAULT = {'d-story-so-far', 'd-the-fall-of-kholinar'}


def _index(*docs):
    return {'docs': list(docs)}


def test_a_fresh_slug_steps_around_the_vault():
    """The reported case: the title the GM wanted is taken by a vault page."""
    desired = lib.slugify_title('Story So Far')
    assert desired == 'd-story-so-far', desired
    got = lib.unique_slug(_index(), desired, reserved=VAULT)
    assert got != desired
    assert got not in VAULT


def test_retyping_the_same_title_now_actually_moves_the_document():
    """The screen says "rename this document to publish it". Retyping the same
    name is the first thing anyone tries, and it used to be a no-op."""
    entry = {'id': 'doc1', 'slug': 'd-story-so-far'}
    again = lib.unique_slug(_index(entry), lib.slugify_title('Story So Far'),
                            ignore_id='doc1', reserved=VAULT)
    assert again != 'd-story-so-far'


def test_it_still_avoids_other_documents():
    """The original job, unchanged."""
    taken = {'id': 'other', 'slug': 'd-lore'}
    got = lib.unique_slug(_index(taken), 'd-lore', reserved=set())
    assert got != 'd-lore'


def test_it_avoids_documents_and_the_vault_at_once():
    other = {'id': 'other', 'slug': 'd-story-so-far-2'}
    got = lib.unique_slug(_index(other), 'd-story-so-far', reserved=VAULT)
    assert got not in VAULT
    assert got != 'd-story-so-far-2'


def test_a_document_keeps_its_own_slug_when_nothing_else_wants_it():
    """ignore_id exists so re-saving a document does not walk its own address."""
    entry = {'id': 'doc1', 'slug': 'd-lore'}
    assert lib.unique_slug(_index(entry), 'd-lore', ignore_id='doc1', reserved=VAULT) == 'd-lore'


def test_no_vault_means_no_reservations():
    """A campaign publishing through the doc lane alone -- which is the common
    case here -- must be completely unaffected."""
    assert lib.unique_slug(_index(), 'd-story-so-far', reserved=set()) == 'd-story-so-far'
    assert lib.unique_slug(_index(), 'd-story-so-far', reserved=None) == 'd-story-so-far'


def test_empty_slugs_in_the_reserved_set_are_ignored():
    """A malformed vault page with no slug must not reserve the empty string."""
    assert lib.unique_slug(_index(), 'd-x', reserved={None, ''}) == 'd-x'


def test_every_unique_slug_call_reserves_the_vault():
    """Upload and rename. Fixing only one leaves the other able to walk back
    into the wall.

    Asserted as "no call site omits reserved=" rather than by counting one
    exact spelling. The rename path now passes a hoisted `vault_slugs`, so a
    test pinned to the literal call would have gone red for a refactor that
    changed nothing -- while still passing for a genuinely unreserved third
    call site, which is the only thing it was ever meant to catch.
    """
    import re
    src = _app_src()
    calls = re.findall(r'unique_slug\((?:[^()]|\([^()]*\))*\)', src, re.S)
    assert len(calls) >= 2, calls
    for call in calls:
        assert 'reserved=' in call, call
        assert ('reserved=_chronicle_vault_slugs()' in call
                or 'reserved=vault_slugs' in call), call
    assert 'vault_slugs = _chronicle_vault_slugs()' in src


# --- the block has to name what is in the way -------------------------------

def _app_src():
    import os
    return open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'app.py'), encoding='utf-8').read()


def test_the_colliding_vault_page_is_identified():
    """"A vault page already uses this address" is true but unactionable -- it
    does not say WHICH page, so a real conflict and a stale manifest look
    identical. Telling them apart took a code trace once."""
    src = _app_src()
    assert 'def _chronicle_vault_page_at(slug):' in src
    body = src[src.index('def _chronicle_vault_page_at('):]
    body = body[:body.index('\ndef ')]
    for field in ("'title'", "'section'", "'source'"):
        assert field in body, field


def test_every_surface_that_reports_the_block_also_reports_the_page():
    """The manage screen, the docs listing and the rename response. Missing one
    means the GM sees a named conflict in one place and an anonymous one in
    another."""
    assert _app_src().count('shadowed_by=_chronicle_vault_page_at(') == 3


def test_the_notice_names_it_and_the_script_repaints_it():
    import os
    tpl = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'templates', 'chronicle_manage.html'), encoding='utf-8').read()
    assert 'js-shadow-who' in tpl
    assert 'd.shadowed_by.title' in tpl
    assert "who.textContent" in tpl, 'a rename must update the name, not leave the old one'


# --- and a PUBLISHED row that the vault shadows has a way out ---------------

def _tpl():
    import os
    return open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'templates', 'chronicle_manage.html'), encoding='utf-8').read()


def test_everything_this_screen_hides_can_actually_be_hidden():
    """An author `display` defeats the UA's [hidden] rule regardless of
    specificity, so a class that sets one turns `hidden` into decoration.

    This was not theoretical. `.chron-notice { display: flex }` had no guard,
    and the shadowed-address notice is rendered with `hidden` on every row that
    has NO collision and repainted by assigning .hidden after a rename. So the
    screen advertised a block on every document, and the one document that
    really had one went on saying so after the rename that cleared it -- the
    fix looked broken at the exact moment it worked.

    Asserted over the whole template rather than the two known elements: the
    map hit this same bug five times, and the next element added here would
    inherit it silently.
    """
    import os
    import re
    tpl = _tpl()
    css = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'static', 'css', 'system.css'), encoding='utf-8').read()
    tags = [t for t in re.findall(r'<[a-z]+\b[^>]*>', tpl, re.S)
            if re.search(r'(?<!aria-)\bhidden\b', t)]
    assert tags, 'no hideable elements found -- did the markup change?'
    checked = 0
    for tag in tags:
        classes = re.search(r'class="([^"]*)"', tag)
        for name in (classes.group(1).split() if classes else []):
            if name.startswith(('js-', '{')):
                continue
            base = re.search(r'\.%s\s*\{[^}]*\bdisplay\s*:' % re.escape(name), css)
            if not base:
                continue
            checked += 1
            assert '.%s[hidden]' % name in css, (
                '.%s sets display, so [hidden] does nothing without a guard' % name)
    assert checked >= 2, 'expected the notice and the frozen-address note'


def test_the_switch_is_disabled_only_for_publishing():
    """It used to be disabled whenever shadowed, published or not -- so a
    published document the vault had taken over could not be switched off, and
    that was the only control that would have stopped players being served the
    wrong page."""
    tpl = _tpl()
    assert 'not d.published and (d.shadowed_by_vault or not d.previewed_at)' in tpl
    assert '{% if d.shadowed_by_vault or not d.previewed_at %}disabled' not in tpl


def test_the_label_separates_blocked_from_overridden():
    """"Blocked" is true of a private document that cannot publish. A published
    one is not blocked -- it is live and losing the address."""
    tpl = _tpl()
    assert 'd.published and d.shadowed_by_vault %}Overridden' in tpl


def test_the_advice_differs_by_publish_state():
    """"Rename this document to publish it" describes a state a published
    document is already past."""
    tpl = _tpl()
    assert 'js-shadow-advice' in tpl
    assert 'switch it off' in tpl, 'the published row must be told its other way out'


def test_the_frozen_address_note_is_hidden_while_shadowed():
    """That note says renaming changes the name only. Now untrue for exactly
    this row, which is the one the GM is being asked to rename."""
    tpl = _tpl()
    assert 'js-frozen-note' in tpl
    assert 'not (d.published and not d.shadowed_by_vault)' in tpl


def test_one_function_owns_the_switch():
    """The rename handler set box.disabled and then called paintPublishState,
    which overwrote it -- so a rename that failed to clear the collision left
    the blocked row's toggle live."""
    tpl = _tpl()
    body = tpl[tpl.index('function paintPublishState('):]
    body = body[:body.index('\n  }')]
    assert 'box.disabled = !doc.published && (blocked || !previewed)' in body
    assert tpl.count('box.disabled =') == 1, 'exactly one writer'
    assert 'disabled = doc.shadowed_by_vault' not in tpl


def test_resubmitting_the_same_title_is_allowed_while_blocked():
    """The screen says "rename this document". Retyping the same name is the
    first thing anyone tries -- and the client used to swallow it as a no-op,
    so the instruction looked broken for the one case it exists for."""
    import os
    tpl = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'templates', 'chronicle_manage.html'), encoding='utf-8').read()
    assert "chron-switch--blocked') !== null" in tpl
    assert 'next === lastTitle && !blocked' in tpl
