"""Guard: no `|tojson` inside a DOUBLE-quoted inline event handler.

Jinja's `tojson` emits a safe (un-autoescaped) JSON string with literal double
quotes. Inside a double-quoted on<event>="..." attribute those quotes close the
attribute early, so the handler renders mangled and the click does NOTHING. This
silently broke the entire Cosmere sheet's tap-to-roll + condition toggles
(onclick="cosRoll("Agility", 3, "spd")" -> the browser saw only `cosRoll(`).

Safe forms: single-quote the attribute (tojson escapes ' to \\u0027), or add
`|forceescape` (escapes the " to &#34;, which the parser decodes back). This test
fails if any template reintroduces the broken double-quoted-without-forceescape form.
"""
import os
import re
import glob

_TPL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'templates')
# A double-quoted on<event> handler whose value contains a |tojson expansion.
_BAD = re.compile(r'on[a-z]+="[^"]*\|\s*tojson[^"]*"')


def test_no_tojson_in_double_quoted_handlers():
    offenders = []
    for path in glob.glob(os.path.join(_TPL, '**', '*.html'), recursive=True):
        with open(path, encoding='utf-8') as f:
            for i, line in enumerate(f, 1):
                for m in _BAD.finditer(line):
                    if 'forceescape' in m.group(0):
                        continue  # |tojson|forceescape is safe in a double-quoted attr
                    offenders.append('%s:%d  %s' % (os.path.basename(path), i, m.group(0)[:90]))
    assert not offenders, (
        "tojson inside a double-quoted inline handler breaks the click (quotes close "
        "the attribute). Single-quote the attribute or add |forceescape:\n" + "\n".join(offenders)
    )
