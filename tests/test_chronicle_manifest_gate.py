"""The publish endpoint is the only door bytes from outside the process come
through, and it was the only one refusing anything less than a malformed slug.

Reported from real use: a Chronicle refused a document upload because "a vault
page already uses the address d-story-so-far". That page was filed under a
section reaching no nav tab, so it was invisible on every tab of the site while
still owning the address -- and establishing that it could not have come from
tools/chronicle_build.py at all took a full code trace.

Two properties the vault lane leans on were claimed in one place and enforced
in none:

  * the SECTION vocabulary. build_manifest raises on an unknown section
    (tools/chronicle_build.py::_SECTIONS) and the doc lane refuses one at
    upload (core.chronicle_docs.SECTIONS), but the ingest validator never
    looked at the field. A page it lets through is counted in the page total
    and owns its slug in _chronicle_vault_slugs() while being unreachable.
  * the 'd-' NAMESPACE. core/chronicle_docs.py states the prefix makes
    cross-lane collisions "structurally impossible". It did not.
    _chronicle_fragment resolves VAULT-FIRST, so a vault page sitting on a
    document's address serves players the wrong page with no error anywhere.

The vault's section list is deliberately WIDER than the doc lane's: 'home',
'atlas' and 'fieldguide' are real vault sections with no nav tab of their own,
and refusing them would break a legitimate publish.
"""
from __future__ import annotations

import json
import os

import pytest

import app
from core import chronicle_docs as lib
from tools import chronicle_build as cb


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _manifest(**page):
    base = {'slug': 'a-page', 'section': 'lore', 'source': 'content/a-page.md'}
    base.update(page)
    return {'schema_version': app.CHRONICLE_SCHEMA_VERSION, 'pages': [base]}


# --- the section vocabulary is enforced at the door -------------------------

def test_the_reported_section_is_refused():
    """'story' is not a section at all -- it is the nav TAB that section
    'recap' lands on (app._CHRONICLE_SECTION_TO_NAV). A page filed under it
    reaches no tab and no reader."""
    ok, err = app._chronicle_validate_manifest(_manifest(section='story'))
    assert not ok
    assert 'story' in err, err


def test_a_page_with_no_section_is_refused():
    """build_manifest always emits one, so only a hand-assembled manifest can
    omit it -- and an absent section filters out of every tab exactly like an
    unknown one."""
    page = {'slug': 'a-page', 'source': 'content/a-page.md'}
    ok, err = app._chronicle_validate_manifest(
        {'schema_version': app.CHRONICLE_SCHEMA_VERSION, 'pages': [page]})
    assert not ok
    assert 'section' in err.lower(), err


@pytest.mark.parametrize('section', lib.VAULT_SECTIONS)
def test_every_vault_section_is_accepted(section):
    ok, err = app._chronicle_validate_manifest(_manifest(section=section))
    assert ok, err


def test_the_error_lists_what_would_have_worked():
    """A refusal that does not name the alternatives sends the GM back to the
    source to find out what it wanted."""
    _, err = app._chronicle_validate_manifest(_manifest(section='nope'))
    for section in lib.VAULT_SECTIONS:
        assert section in err, (section, err)


# --- the doc lane's namespace is refused ------------------------------------

def test_a_vault_page_may_not_claim_the_document_namespace():
    ok, err = app._chronicle_validate_manifest(_manifest(slug='d-story-so-far'))
    assert not ok
    assert 'd-story-so-far' in err, err


def test_the_refusal_is_the_prefix_not_the_one_reported_slug():
    ok, _ = app._chronicle_validate_manifest(_manifest(slug='d-anything-else'))
    assert not ok


def test_a_slug_merely_starting_with_d_is_fine():
    """The reservation is the PREFIX 'd-', not the letter. 'dragons' and
    'd20-house-rules' are ordinary vault pages."""
    for slug in ('dragons', 'd20-house-rules', 'duke-of-ash'):
        ok, err = app._chronicle_validate_manifest(_manifest(slug=slug))
        assert ok, (slug, err)


# --- nothing that legitimately worked before is refused now -----------------

def test_the_shipped_sample_manifest_still_validates():
    """tests/fixtures/chronicle_sample is the archive the publish tests POST.
    It uses section 'home', which reaches no nav tab and must stay legal."""
    path = os.path.join(_ROOT, 'tests', 'fixtures', 'chronicle_sample', 'manifest.json')
    with open(path, encoding='utf-8') as f:
        manifest = json.load(f)
    assert any(p.get('section') == 'home' for p in manifest['pages']), (
        'the fixture stopped covering the no-nav-tab case')
    ok, err = app._chronicle_validate_manifest(manifest)
    assert ok, err


def test_real_build_output_validates_for_every_section():
    """The cross-component contract, per section rather than once: the tool
    that produces manifests and the door that accepts them must agree on the
    whole vocabulary, not just the one value a sample happens to use."""
    for section in lib.VAULT_SECTIONS:
        manifest = cb.build_manifest(
            'c' * 32, 4,
            [{'slug': 'page-%s' % section, 'section': section, 'title': 'T', 'body': ''}],
            [], [], {})
        ok, err = app._chronicle_validate_manifest(manifest)
        assert ok, (section, err)


# --- the two halves cannot drift --------------------------------------------

def test_the_section_vocabularies_agree():
    """tools/chronicle_build.py is stdlib-only on purpose -- it runs standalone
    against the GM's vault -- so it cannot import core.chronicle_docs. The
    duplication is deliberate; the drift is what has to be caught."""
    assert set(cb._SECTIONS) == set(lib.VAULT_SECTIONS)


def test_the_reserved_prefixes_agree():
    assert cb._RESERVED_SLUG_PREFIX == lib.SLUG_PREFIX


def test_the_doc_sections_stay_a_subset_of_the_vault_sections():
    """A document filed under a section the vault lane would refuse could never
    be published through the union at read time."""
    assert set(lib.SECTIONS) <= set(lib.VAULT_SECTIONS)


# --- and the build tool never mints a slug the door would refuse ------------

def test_slugify_pushes_a_reserved_slug_back_out():
    """"D. Story So Far" slugifies to 'd-story-so-far' quite honestly. Refusing
    that at ingest without this would abort the GM's whole build over a
    namespace they have no reason to know exists."""
    assert cb.slugify('D. Story So Far') == 'page-d-story-so-far'
    assert cb.slugify('D - Anything') == 'page-d-anything'


def test_slugify_never_returns_the_reserved_prefix():
    titles = ['D. Story So Far', 'd-already-hyphenated', 'D', 'D!!! Ruins',
              'Story So Far', 'The Fall of Kholinar', '', None, '...',
              'Dragons', 'D20 House Rules']
    for title in titles:
        assert not cb.slugify(title).startswith(cb._RESERVED_SLUG_PREFIX), title


def test_slugify_output_always_survives_the_real_validator():
    """Round-trip: whatever slugify mints must pass the door it is aimed at."""
    for title in ['D. Story So Far', 'D', 'd-x', 'Ordinary Page', 'D20 Rules']:
        slug = cb.slugify(title)
        ok, err = app._chronicle_validate_manifest(_manifest(slug=slug))
        assert ok, (title, slug, err)


def test_a_long_reserved_title_stays_inside_the_slug_ceiling():
    """The push-out prepends five characters, and the manifest regex caps the
    whole slug at 81."""
    slug = cb.slugify('D. ' + ' '.join(['word'] * 40))
    assert len(slug) <= 81
    assert cb._SLUG_OK.match(slug), slug
    assert not slug.endswith('-')
