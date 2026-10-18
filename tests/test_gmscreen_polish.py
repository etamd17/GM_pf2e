from html.parser import HTMLParser
import json
import re
import shutil
import subprocess

import pytest

import app


class _GroupCardCounter(HTMLParser):
    def __init__(self):
        super().__init__()
        self._group_stack = []
        self.matches = 0

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "").split()
        inside_group = bool(self._group_stack and self._group_stack[-1])
        is_group = "gm-group-grid" in classes
        self._group_stack.append(inside_group or is_group)
        if (inside_group or is_group) and "gm-ref-card" in classes:
            self.matches += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self._group_stack:
            self._group_stack.pop()


def _render(monkeypatch) -> str:
    monkeypatch.setattr(app, "_active_system", lambda: "pf2e")
    monkeypatch.setattr(app, "_account_mode", lambda: False)
    monkeypatch.setattr(app, "GM_PASSWORD", "")
    response = app.app.test_client().get("/gmscreen")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_polish_script_targets_rendered_cards_and_keeps_their_group(monkeypatch):
    html = _render(monkeypatch)
    selector = re.search(
        r"var cards = Array\.prototype\.slice\.call\(document\.querySelectorAll\('([^']+)'\)\)",
        html,
    )
    assert selector and selector.group(1) == ".gm-group-grid .gm-ref-card"

    counter = _GroupCardCounter()
    counter.feed(html)
    assert counter.matches > 0
    assert "card._gmGroup = card.parentElement;" in html
    assert "c._gmGroup.appendChild(c);" in html
    assert "grid.appendChild" not in html


def test_gmscreen_controller_runtime(monkeypatch):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the GM Screen controller behavior test")

    html = _render(monkeypatch)
    match = re.search(r"<script\s+data-gmscreen-controller>(.*?)</script>", html, re.S)
    assert match, "GM Screen controller must be isolated for direct behavior testing"
    drawer_functions = re.search(
        r"(function setChecksDrawerOpen\(open\).*?document\.addEventListener\('keydown', e => \{.*?\n    \}\);)\n    // Remove the intro glow-pulse",
        html,
        re.S,
    )
    assert drawer_functions, "drawer state helpers must be available for behavior testing"
    controller_code = drawer_functions.group(1) + "\n" + match.group(1)

    harness = r'''
const vm = require('node:vm');
const assert = require('node:assert/strict');
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', () => {
  const {code} = JSON.parse(input);
  let focused = null;
  const detailCalls = [];
  const documentListeners = {};
  const storage = new Map([['gmscreen_pinned_v1', JSON.stringify(['undead-environmental-damage'])]]);

  class Classes {
    constructor(value = '') { this.values = new Set(value.split(/\s+/).filter(Boolean)); }
    add(...names) { names.forEach(name => this.values.add(name)); }
    remove(...names) { names.forEach(name => this.values.delete(name)); }
    contains(name) { return this.values.has(name); }
    toggle(name, force) {
      const next = force === undefined ? !this.contains(name) : Boolean(force);
      if (next) this.add(name); else this.remove(name);
      return next;
    }
  }

  function matches(el, selector) {
    if (selector.startsWith('#')) return el.id === selector.slice(1);
    const classMatch = selector.match(/^\.([\w-]+)/);
    if (classMatch && !el.classList.contains(classMatch[1])) return false;
    const dataMatch = selector.match(/\[data-reference-topic(?:="([^"]+)")?\]/);
    if (dataMatch) {
      if (!('referenceTopic' in el.dataset)) return false;
      if (dataMatch[1] && el.dataset.referenceTopic !== dataMatch[1]) return false;
    }
    return Boolean(classMatch || dataMatch);
  }

  function element({tag = 'div', cls = '', id = '', dataset = {}, text = ''} = {}) {
    const el = {
      tagName: tag.toUpperCase(), id, dataset: {...dataset}, attrs: {}, hidden: false,
      value: '', textContent: text, childNodes: [], children: [], parentElement: null,
      listeners: {}, classList: new Classes(cls), tabIndex: 0,
      setAttribute(name, value) { this.attrs[name] = String(value); },
      getAttribute(name) { return this.attrs[name] ?? null; },
      addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); },
      removeEventListener(name, fn) {
        this.listeners[name] = (this.listeners[name] || []).filter(item => item !== fn);
      },
      appendChild(child) {
        if (child.parentElement) {
          child.parentElement.children = child.parentElement.children.filter(item => item !== child);
        }
        this.children.push(child); child.parentElement = this; return child;
      },
      append(child) { return this.appendChild(child); },
      click() {
        for (const fn of this.listeners.click || []) fn({
          target: this, currentTarget: this, stopPropagation() {}
        });
      },
      focus() { focused = this; },
      contains(target) {
        return target === this || this.children.some(child => child.contains && child.contains(target));
      },
      closest(selector) {
        for (let cursor = this; cursor; cursor = cursor.parentElement) {
          if (matches(cursor, selector)) return cursor;
        }
        return null;
      },
      querySelector(selector) {
        return this.querySelectorAll(selector)[0] || null;
      },
      querySelectorAll(selector) {
        if (selector === '.gm-reference-tab') return tabs;
        const found = [];
        const walk = parent => parent.children.forEach(child => {
          if (matches(child, selector)) found.push(child);
          walk(child);
        });
        walk(this); return found;
      },
      scrollIntoView() {
        if (this.kind === 'card') {
          const group = this._gmGroup && this._gmGroup.parentElement;
          assert.equal(group && group.hidden, false, 'search must reveal a matching group before scrolling');
        }
        this.scrolled = true;
      }
    };
    Object.defineProperty(el, 'className', {
      get() { return [...this.classList.values].join(' '); },
      set(value) { this.classList = new Classes(value); }
    });
    return el;
  }

  function fire(el, name, event = {}) {
    const payload = {target: el, currentTarget: el, ...event};
    for (const fn of el.listeners[name] || []) fn(payload);
  }
  function key(el, key) {
    let prevented = false;
    fire(el, 'keydown', {key, preventDefault() { prevented = true; }});
    return prevented;
  }

  const topics = ['checks', 'conditions', 'adversaries', 'exploration'];
  const groups = topics.map(topic => element({cls: 'gm-group', dataset: {referenceTopic: topic}}));
  const grids = groups.map(group => {
    const grid = element({cls: 'gm-group-grid'}); group.appendChild(grid); return grid;
  });
  function card(groupIndex, title, text) {
    const heading = element({tag: 'h2', cls: 'gm-ref-header', text: title});
    heading.childNodes = [{textContent: title}];
    const item = element({cls: 'gm-ref-card', text});
    item.kind = 'card'; item.heading = heading; item.appendChild(heading);
    grids[groupIndex].appendChild(item); return item;
  }
  const cards = [
    card(0, 'Difficulty Classes', 'Difficulty classes, damage guidance, and Treat Wounds'),
    card(0, 'Combat Maneuvers', 'Grapple, Shove, and Trip'),
    card(1, 'Common Conditions', 'Frightened and Sickened'),
    card(2, 'Undead & Environmental Damage', 'Undead, void healing, damage, falling, and fire'),
    card(2, 'Material Hardness', 'Material hardness and object defenses'),
    card(3, 'Travel & Cover', 'Travel speed, cover, light, and senses'),
  ];
  const condition = element({tag: 'dt', text: 'Frightened X'});
  const search = element({tag: 'input', id: 'comp-search'});
  const detail = element({id: 'comp-detail'});
  const nav = element({tag: 'nav', cls: 'gm-reference-nav'});
  const checksButton = element({tag: 'button', cls: 'gm-checks-button'});
  checksButton.setAttribute('aria-expanded', 'false');
  const drawer = element({id: 'checks-drawer', cls: 'checks-drawer'});
  const tabs = [...topics, 'all'].map(topic => {
    const tab = element({tag: 'button', cls: 'gm-reference-tab', dataset: {referenceTopic: topic}});
    nav.appendChild(tab); return tab;
  });

  const document = {
    activeElement: null, readyState: 'complete',
    addEventListener(name, fn) {
      (documentListeners[name] ||= []).push(fn);
      if (name === 'DOMContentLoaded') fn();
    },
    createElement(tag) { return element({tag}); },
    getElementById(id) {
      if (id === 'comp-search') return search;
      if (id === 'comp-detail') return detail;
      if (id === 'checks-drawer') return drawer;
      return null;
    },
    querySelector(selector) {
      if (selector === '.gm-reference-nav') return nav;
      if (selector === '.gm-checks-button') return checksButton;
      const topic = selector.match(/^\.gm-group\[data-reference-topic="([^"]+)"\]$/);
      if (topic) return groups.find(group => group.dataset.referenceTopic === topic[1]) || null;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === '.gm-group-grid .gm-ref-card') return cards;
      if (selector === '.gm-reference-tab' || selector === '.gm-reference-nav .gm-reference-tab') return tabs;
      if (selector === '.gm-group[data-reference-topic]') return groups;
      if (selector === '.gm-cond dt') return [condition];
      return [];
    }
  };
  const context = {
    document,
    localStorage: {
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      setItem(key, value) { storage.set(key, value); }
    },
    setTimeout(fn) { fn(); return 1; }, clearTimeout() {},
    requestAnimationFrame(fn) { fn(); },
    showCompDetail(name, type) { detailCalls.push([name, type]); },
    console
  };
  context.window = context;
  vm.runInNewContext(code, context);

  function documentKey(keyValue, target = element({tag: 'div'}), modifiers = {}) {
    let prevented = false;
    const event = {
      key: keyValue, target, ctrlKey: false, metaKey: false, altKey: false,
      preventDefault() { prevented = true; }, ...modifiers
    };
    for (const fn of documentListeners.keydown || []) fn(event);
    return prevented;
  }

  fire(checksButton, 'click');
  assert.equal(drawer.classList.contains('open'), true);
  assert.equal(checksButton.attrs['aria-expanded'], 'true');
  context.closeChecksDrawer();
  assert.equal(documentKey('c'), true);
  assert.equal(drawer.classList.contains('open'), true);
  assert.equal(checksButton.attrs['aria-expanded'], 'true');
  assert.equal(documentKey('Escape'), true);
  assert.equal(drawer.classList.contains('open'), false);
  assert.equal(checksButton.attrs['aria-expanded'], 'false');
  assert.equal(documentKey('c', element({tag: 'input'})), false);
  assert.equal(drawer.classList.contains('open'), false);
  const editor = element({tag: 'div'});
  editor.isContentEditable = true;
  assert.equal(documentKey('c', editor), false);
  assert.equal(documentKey('c', element({tag: 'div'}), {ctrlKey: true}), false);
  assert.equal(documentKey('c', element({tag: 'div'}), {metaKey: true}), false);
  assert.equal(documentKey('c', element({tag: 'div'}), {altKey: true}), false);
  assert.equal(drawer.classList.contains('open'), false);
  assert.equal(documentKey('C'), true);
  assert.equal(drawer.classList.contains('open'), true);
  assert.equal(documentKey('Escape'), true);
  assert.equal(drawer.classList.contains('open'), false);
  assert.equal(checksButton.attrs['aria-expanded'], 'false');
  context.toggleChecksDrawer();
  assert.equal(drawer.classList.contains('open'), true);
  assert.equal(checksButton.attrs['aria-expanded'], 'true');
  context.closeChecksDrawer();

  function selected(topic) {
    groups.forEach(group => assert.equal(group.hidden, topic !== 'all' && group.dataset.referenceTopic !== topic));
    tabs.forEach(tab => {
      const active = tab.dataset.referenceTopic === topic;
      assert.equal(tab.attrs['aria-pressed'], String(active));
      assert.equal(tab.tabIndex, active ? 0 : -1);
    });
  }

  selected('checks');
  fire(tabs[1], 'click'); selected('conditions');
  fire(tabs[4], 'click'); selected('all');

  assert.equal(key(tabs[4], 'ArrowRight'), true); selected('checks'); assert.equal(focused, tabs[0]);
  assert.equal(key(tabs[0], 'ArrowLeft'), true); selected('all'); assert.equal(focused, tabs[4]);
  assert.equal(key(tabs[4], 'ArrowDown'), true); selected('checks'); assert.equal(focused, tabs[0]);
  assert.equal(key(tabs[0], 'ArrowUp'), true); selected('all'); assert.equal(focused, tabs[4]);
  assert.equal(key(tabs[4], 'Home'), true); selected('checks'); assert.equal(focused, tabs[0]);
  assert.equal(key(tabs[0], 'End'), true); selected('all'); assert.equal(focused, tabs[4]);
  assert.equal(key(tabs[4], 'Tab'), false); selected('all');

  fire(tabs[0], 'click'); selected('checks');
  search.value = 'undead'; fire(search, 'input');
  selected('adversaries');
  assert.equal(cards[3].scrolled, true);
  search.value = ''; fire(search, 'input');
  selected('checks');
  assert.equal(cards.some(item => item.classList.contains('ref-match')), false);

  fire(tabs[1], 'click'); selected('conditions');
  cards.forEach(item => { item.scrolled = false; });
  search.value = 'damage'; fire(search, 'input');
  selected('all');
  const sharedMatches = cards.filter(item => item.classList.contains('ref-match'));
  assert.deepEqual(sharedMatches, [cards[0], cards[3]]);
  sharedMatches.forEach(item => {
    assert.equal(item.closest('.gm-group[data-reference-topic]').hidden, false);
  });
  assert.equal(cards[0].scrolled, true);
  assert.equal(cards[3].scrolled, false);
  search.value = ''; fire(search, 'input');
  selected('conditions');
  assert.equal(cards.some(item => item.classList.contains('ref-match')), false);

  cards.forEach((item, index) => assert.equal(item.parentElement, grids[[0, 0, 1, 2, 2, 3][index]]));
  assert.equal(cards[3].classList.contains('is-pinned'), true);
  const undeadPin = cards[3].querySelector('.gm-pin-btn');
  const hardnessPin = cards[4].querySelector('.gm-pin-btn');
  assert.ok(undeadPin && hardnessPin);
  assert.equal(undeadPin.innerHTML, '★');
  assert.equal(hardnessPin.innerHTML, '☆');
  hardnessPin.click();
  assert.deepEqual(JSON.parse(storage.get('gmscreen_pinned_v1')), [
    'undead-environmental-damage',
    'material-hardness'
  ]);
  assert.equal(cards[3].classList.contains('is-pinned'), true);
  assert.equal(cards[4].classList.contains('is-pinned'), true);
  assert.deepEqual(grids[2].children, [cards[3], cards[4]]);
  undeadPin.click();
  assert.deepEqual(JSON.parse(storage.get('gmscreen_pinned_v1')), ['material-hardness']);
  assert.equal(cards[3].classList.contains('is-pinned'), false);
  assert.equal(cards[4].classList.contains('is-pinned'), true);
  assert.equal(undeadPin.innerHTML, '☆');
  assert.equal(undeadPin.title, 'Pin this card to the top');
  assert.equal(hardnessPin.innerHTML, '★');
  assert.equal(hardnessPin.title, 'Unpin');
  assert.deepEqual(grids[2].children, [cards[4], cards[3]]);
  assert.equal(cards[3].parentElement, grids[2]);
  assert.equal(cards[4].parentElement, grids[2]);

  fire(condition, 'click');
  assert.deepEqual(detailCalls, [['Frightened', 'condition']]);
  console.log('GM Screen controller behavior passed');
});
'''
    result = subprocess.run(
        [node, "-e", harness],
        input=json.dumps({"code": controller_code}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "GM Screen controller behavior passed"
