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


def test_both_call_sites_pass_the_vault():
    """Upload and rename. Fixing only one leaves the other able to walk back
    into the wall."""
    import os
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'app.py'), encoding='utf-8').read()
    assert src.count('reserved=_chronicle_vault_slugs()') == 2
