"""Cosmere GM generators -- the content module produces valid HTML for every
type across many rolls (catches empty pools / index errors in the random paths)."""
import systems.cosmere.generator as gen


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
