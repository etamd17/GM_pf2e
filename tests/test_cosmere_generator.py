"""Cosmere GM generators -- the content module produces valid HTML for every
type across many rolls (catches empty pools / index errors in the random paths)."""
import pytest

import systems.cosmere.generator as gen
import systems.cosmere.generator_scadrial as scadrial


ROSHAR_ONLY = (
    'roshar', 'alethi', 'thaylen', 'highstorm', 'sphere', 'spren',
    'shardblade', 'fabrial', 'chasmfiend', 'urithiru', 'kholinar',
    'herdazian', 'veden', 'azish', 'broam', 'rockbud', 'stormfather',
)


def _roshar_terms(text):
    folded = text.casefold()
    return [term for term in ROSHAR_ONLY if term in folded]


def test_expected_generator_types():
    expected = {'name', 'npc', 'weather', 'spheres', 'loot',
                'location', 'hook', 'rumor', 'dressing'}
    assert expected <= set(gen.GENERATORS)


def test_every_generator_returns_html():
    assert gen.GENERATORS, 'no generators registered'
    for key, (label, fn) in gen.GENERATORS.items():
        assert label
        for _ in range(40):                 # exercise the randomness
            html = fn()
            assert isinstance(html, str) and html.strip(), key
        assert gen.generate(key).strip(), key
    assert gen.generate('does-not-exist') == ''


def test_name_culture_filter():
    for _ in range(10):
        assert 'Alethi' in gen.gen_name('Alethi')   # requested culture honored
    assert gen.gen_name('Nonsense')                  # unknown -> random, still non-empty


def test_mistborn_generators_never_emit_roshar_content():
    gen.random.seed(20261015)
    for key in gen.GENERATORS:
        for _ in range(60):
            html = gen.generate(key, world='mistborn')
            assert html.strip(), key
            assert not _roshar_terms(html), (key, html)


def test_stormlight_default_and_unknown_world_keep_existing_output():
    for key in gen.GENERATORS:
        gen.random.seed(1701)
        default = gen.generate(key)
        gen.random.seed(1701)
        explicit = gen.generate(key, world='stormlight')
        gen.random.seed(1701)
        unknown = gen.generate(key, world='unknown')
        assert default == explicit == unknown, key


def test_mistborn_era_two_changes_npcs_locations_and_coins():
    gen.random.seed(1902)
    samples = {
        key: ' '.join(gen.generate(key, world='mistborn', era='era-2')
                      for _ in range(80)).casefold()
        for key in ('npc', 'location', 'spheres')
    }
    assert any(word in samples['npc'] for word in ('constable', 'gunsmith', 'rail'))
    assert any(word in samples['location'] for word in ('elendel', 'rail', 'canal'))
    assert 'banknote' in samples['spheres']


@pytest.mark.parametrize(('era', 'rare', 'source', 'expected'), (
    ('era-1', False, 'a weighted coin pouch prepared for a Coinshot',
     'A weighted coin pouch prepared for a Coinshot.'),
    ('era-1', False, 'a canal key stamped with a Ministry mark',
     'A canal key stamped with a Ministry mark.'),
    ('era-1', True, 'a map marking a cache beneath Kredik Shaw',
     'A map marking a cache beneath Kredik Shaw.<br>'
     '<span class="muted">A rare find that will attract determined buyers.</span>'),
    ('era-2', False, 'a survey map of an abandoned Roughs mine',
     'A survey map of an abandoned Roughs mine.'),
))
def test_scadrial_loot_preserves_internal_capitalization(
        monkeypatch, era, rare, source, expected):
    monkeypatch.setattr(scadrial.random, 'random', lambda: 0.0 if rare else 1.0)
    monkeypatch.setattr(scadrial.random, 'choice', lambda _items: source)

    assert scadrial.gen_loot(era=era) == expected


def test_mistborn_era_two_names_use_only_era_two_groups():
    gen.random.seed(20261015)
    for generator in (gen.gen_name, gen.gen_npc):
        results = ' '.join(generator(world='mistborn', era='era-2')
                           for _ in range(100))
        assert 'Noble House' not in results
        assert 'Skaa' not in results
        assert 'Terris' not in results
        assert 'Elendel Basin' in results
        assert 'Roughs' in results
