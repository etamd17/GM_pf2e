"""5etools entry renderer + the committed content database."""
import json
import os
import sqlite3

import pytest

from systems.dnd5e import content
from systems.dnd5e.render import render_entries, render_text, strip_tags

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pytestmark = pytest.mark.skipif(not content.available(), reason='dnd5e.db not built')


def test_attack_line_renders_rolls_and_labels():
    h = render_text('{@atkr m} {@hit 4}, reach 5 ft. {@h}5 ({@damage 1d6 + 2}) Slashing damage.')
    assert '<em class="d5-lbl">Melee Attack Roll:</em>' in h
    assert 'data-roll="1d20+4"' in h and 'data-roll="1d6+2"' in h
    assert '<em class="d5-lbl">Hit:</em>' in h


def test_reference_tags_default_to_5etools_source():
    h = render_text('{@spell fireball} and {@spell Fireball|XPHB|the new one} and {@condition prone}')
    assert 'data-id="fireball|phb"' in h                 # untagged spell = PHB
    assert 'data-id="fireball|xphb"' in h and '>the new one</a>' in h
    assert 'data-kind="condition" data-id="prone|phb"' in h


def test_everything_is_escaped():
    h = render_text('<script>alert(1)</script> {@b <img src=x onerror=1>}')
    assert '<script>' not in h and '<img' not in h
    assert '&lt;script&gt;' in h


def test_nested_tags_and_unknown_tags_degrade():
    assert strip_tags('{@b bold {@condition prone}} {@nosuchtag hello|x}') == 'bold prone hello'
    assert render_text('{@unbalanced') == '{@unbalanced'


def test_every_record_renders_without_falling_back():
    """No statblock/feature in the shipped data hits the 'unrenderable' path."""
    bad = []
    for kind in ('class', 'subclass', 'spell', 'race', 'background', 'feat', 'optionalfeature', 'condition'):
        for rec in content.all_records(kind):
            h = render_entries(rec.get('entries'))
            if 'unrenderable' in h or '{@' in h:
                bad.append((kind, rec['name']))
    assert bad == []


def test_db_matches_resolved_export(tmp_path):
    """The committed DB must be what build_dnd5e_db.py produces from the
    committed resolved export -- i.e. nobody edited one without the other."""
    from tools.dnd5e.build_dnd5e_db import build
    out = tmp_path / 'rebuilt.db'
    counts = build(os.path.join(ROOT, 'dnd5e_data', 'resolved'), str(out))
    assert json.loads(content.meta()['counts']) == counts
    a = sqlite3.connect(content.paths.db_path())
    b = sqlite3.connect(str(out))
    q = 'SELECT kind, id, data FROM entity ORDER BY kind, id'
    assert a.execute('SELECT COUNT(*) FROM entity').fetchone() == b.execute('SELECT COUNT(*) FROM entity').fetchone()
    assert a.execute(q).fetchall() == b.execute(q).fetchall()
    a.close()
    b.close()


def test_content_queries():
    gob = content.search_monsters(q='goblin warrior', edition='one')
    assert gob and gob[0]['cr'] == '1/4' and gob[0]['xp'] == 50
    fireballs = content.search_spells(class_name='Wizard', class_source='XPHB', levels=[3], q='fireball')
    assert [s['name'] for s in fireballs] == ['Fireball']
    assert content.get_ref('item', 'longsword|xphb')['dmg1'] == '1d8'
    assert len(content.subclasses_for('Fighter', 'XPHB')) >= 4
