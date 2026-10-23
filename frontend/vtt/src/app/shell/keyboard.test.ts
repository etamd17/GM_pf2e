// @vitest-environment jsdom
import { afterEach, expect, it } from 'vitest';
import { hasMapToolFocus, ignoresMapKey, TOOL_KEYS } from './keyboard';
afterEach(() => { document.body.innerHTML = ''; });
it.each(['input', 'textarea', 'select', 'div contenteditable="true"', 'div contenteditable=""'])('leaves typing in %s alone', markup => {
  const [tag, ...attrs] = markup.split(' ');
  document.body.innerHTML = `<${tag} ${attrs.join(' ')}><span></span></${tag}>`;
  const target = document.body.firstElementChild!;
  const e = new KeyboardEvent('keydown', {key:'v', bubbles:true});
  target.dispatchEvent(e);
  expect(ignoresMapKey(e)).toBe(true);
});
it('recognizes descendants of editable ancestors', () => {
  document.body.innerHTML = '<div contenteditable="true"><span>x</span></div>';
  const e = new KeyboardEvent('keydown', {key:'Delete'});
  document.querySelector('span')!.dispatchEvent(e);
  expect(ignoresMapKey(e)).toBe(true);
});
it.each([{ctrlKey:true}, {metaKey:true}, {altKey:true}, {isComposing:true}, {repeat:true}, {keyCode:229}])('ignores composing/modifier/repeat %j', init => {
  expect(ignoresMapKey(new KeyboardEvent('keydown', {key:'Delete', ...init}))).toBe(true);
});
it('requires map or tool focus and exposes only the approved keys', () => {
  document.body.innerHTML = '<nav class="vtt-rail"><button></button></nav><aside><button></button></aside>';
  expect(hasMapToolFocus(document.querySelector('nav button'))).toBe(true);
  expect(hasMapToolFocus(document.querySelector('aside button'))).toBe(false);
  expect(hasMapToolFocus(window)).toBe(false);
  expect(Object.keys(TOOL_KEYS)).toEqual(['v','p','m','a','w','f','r']);
});
