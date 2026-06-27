"""Guard: inline onclick handlers must escape values that carry the PC name.

Bug: a PC named "Go'el Thrall" broke the prepared-spell Cast/Undo buttons. The
expendKey is `cast_prep_<pcName>_c..._l..._i...`, embedded raw inside an onclick
attribute's single-quoted JS string. The apostrophe in "Go'el" closed the string
early -> SyntaxError -> the button did nothing, so players "couldn't add a spell
back" after casting. Fix: escape the key the same way the file already escapes
spell names (`.replace(/'/g, "\\'")`).

This is a JS-in-template runtime bug pytest can't execute, so guard the source:
no onclick that calls castPrepSpell/uncastSpell may interpolate the key raw.
"""
import os
import re

_HTML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'templates', 'player_sheet.html')


def test_prepared_cast_handlers_escape_expendkey():
    html = open(_HTML, encoding='utf-8').read()
    # Any onclick invoking the prepared cast/uncast helpers...
    handlers = re.findall(r'onclick="[^"]*(?:castPrepSpell|uncastSpell)\([^"]*"', html)
    assert handlers, "expected to find castPrepSpell/uncastSpell onclick handlers"
    raw = [h[:140] for h in handlers if '${expendKey}' in h]
    assert not raw, (
        "expendKey embedded UNescaped in an inline handler — an apostrophe in the "
        "PC name (e.g. \"Go'el\") will break it. Escape with .replace(/'/g, ...):\n"
        + "\n".join(raw)
    )
    # And the escaped form must actually be present (proves the fix is wired).
    assert any('${expendKey.replace' in h for h in handlers), \
        "no escaped ${expendKey.replace(...)} found in cast/uncast handlers"
