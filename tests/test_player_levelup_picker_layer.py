import re
from pathlib import Path


_TEMPLATE = Path('templates/player_levelup.html').read_text(encoding='utf-8')


def _rule(selector):
    match = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', _TEMPLATE)
    assert match, f'missing CSS rule for {selector}'
    return match.group(1)


def test_picker_disables_the_whole_gains_rail_except_the_override_switch():
    assert 'pointer-events: none' in _rule('body.picker-open #gains-rail')
    assert 'pointer-events: auto' in _rule('body.picker-open #gm-override-switch')
    assert not re.search(
        r'body\.picker-open\s+#gains-rail-header\s*\{[^}]*pointer-events\s*:\s*auto',
        _TEMPLATE,
    )
