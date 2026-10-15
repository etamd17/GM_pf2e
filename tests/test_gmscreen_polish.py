from html.parser import HTMLParser
import re

import app


class _GroupCardCounter(HTMLParser):
    def __init__(self):
        super().__init__()
        self._group_stack = []
        self.matches = 0

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get('class', '').split()
        inside_group = bool(self._group_stack and self._group_stack[-1])
        is_group = 'gm-group-grid' in classes
        self._group_stack.append(inside_group or is_group)
        if (inside_group or is_group) and 'gm-ref-card' in classes:
            self.matches += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self._group_stack:
            self._group_stack.pop()


def test_polish_script_targets_rendered_cards_and_keeps_their_group(monkeypatch):
    monkeypatch.setattr(app, '_active_system', lambda: 'pf2e')
    response = app.app.test_client().get('/gmscreen')

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    selector = re.search(
        r"var cards = Array\.prototype\.slice\.call\(document\.querySelectorAll\('([^']+)'\)\)",
        html,
    )
    assert selector and selector.group(1) == '.gm-group-grid .gm-ref-card'

    counter = _GroupCardCounter()
    counter.feed(html)
    assert counter.matches > 0
    assert 'card._gmGroup = card.parentElement;' in html
    assert 'c._gmGroup.appendChild(c);' in html
    assert 'grid.appendChild' not in html
