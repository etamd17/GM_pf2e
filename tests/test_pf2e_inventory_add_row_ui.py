"""UI-17: the existing sheet add controls have a phone layout override."""
from pathlib import Path


def test_add_row_has_scoped_phone_rules():
    sheet = Path(__file__).resolve().parents[1] / 'templates' / 'player_sheet.html'
    source = sheet.read_text(encoding='utf-8')
    block = source.split('/* UI-17 ADD ROW', 1)[1].split('</style>', 1)[0]
    desktop, mobile = block.split('@media (max-width: 480px)', 1)
    assert 'body .sheet-ember-host #add-item-name' in desktop
    assert 'min-width: 0;' in desktop
    assert 'body .sheet-ember-host div:has(> #add-item-name)' in mobile
    assert 'flex-wrap: wrap;' in mobile
    assert 'flex-basis: 100%;' in mobile
    assert 'min-height: 40px;' in mobile
    assert 'flex-shrink: 0;' in mobile
