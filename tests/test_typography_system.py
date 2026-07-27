"""Guards for the app's typography system.

Two regression classes this pins down, both of which actually shipped:

1. **A smuggled-in extra typeface.** Standalone pages hardcode font families
   instead of using the `--font-display` / `--font-ui` tokens, so app-wide font
   sweeps silently skip them. Three separate pages had each quietly pulled in
   their own serif: the character builder loaded Crimson Text, the splash page
   loaded EB Garamond, and the tracker referenced Crimson Pro.

2. **A dead `@import` in `extra_head`.** `base.html` injects `{% block
   extra_head %}` *inside* its `<style>` element, after other rules. CSS requires
   `@import` to precede all other rules, so a font `@import` written there is
   invalid and silently dropped -- the font is never requested and the text falls
   back to a system font. The tracker shipped this way: it `@import`ed Crimson
   Pro + JetBrains Mono, neither was ever fetched, and its flavor text rendered
   in plain serif. Child templates must use `{% block head_extra %}` (a real
   `<link>`, emitted after system.css) instead.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / 'templates'
SYSTEM_CSS = REPO / 'static' / 'css' / 'system.css'

# The only font families allowed anywhere in the app.
#   Cinzel  -- display / headings            (--font-display)
#   Inter   -- all UI text, labels, numbers  (--font-ui)
#   Alegreya-- long-form reading prose       (--font-flavor)
# Plus the two Cosmere world-skin display faces, loaded only on the Cosmere side.
APPROVED_FAMILIES = {
    'Cinzel',
    'Inter',
    'Alegreya',
    'Cormorant Garamond',   # Cosmere / Stormlight skin
    'Playfair Display',     # Cosmere / Mistborn skin
}

# Quoted names that are generic stacks or non-font strings, not typefaces.
GENERIC_OR_FALLBACK = {
    'Segoe UI', 'Times New Roman', 'Iowan Old Style', 'Helvetica Neue',
    'Courier New', 'Apple Color Emoji', 'Segoe UI Emoji',
}


def _font_families_in(text):
    """Quoted family names appearing in font-family declarations or Google
    Fonts URLs."""
    found = set()
    for decl in re.findall(r'font-family:[^;}\n]*', text):
        found |= set(re.findall(r"""['"]([A-Za-z][A-Za-z ]+)['"]""", decl))
    for fam in re.findall(r'family=([A-Za-z+]+)', text):
        found.add(fam.replace('+', ' '))
    return {f.strip() for f in found if f.strip()}


def _all_source_files():
    files = sorted(TEMPLATES.rglob('*.html'))
    if SYSTEM_CSS.exists():
        files.append(SYSTEM_CSS)
    return files


@pytest.mark.parametrize('path', _all_source_files(), ids=lambda p: p.name)
def test_no_unapproved_typefaces(path):
    """A new page must not introduce a typeface outside the design system."""
    text = path.read_text(encoding='utf-8')
    used = _font_families_in(text)
    unapproved = sorted(used - APPROVED_FAMILIES - GENERIC_OR_FALLBACK)
    assert not unapproved, (
        f"{path.name} introduces typeface(s) outside the design system: {unapproved}.\n"
        f"Use the tokens instead: var(--font-display) for headings, var(--font-ui) for UI "
        f"text, var(--font-flavor) for long-form prose. If a new face is genuinely "
        f"intended, add it to APPROVED_FAMILIES here so the choice is deliberate."
    )


@pytest.mark.parametrize(
    'path',
    [p for p in sorted(TEMPLATES.rglob('*.html'))],
    ids=lambda p: p.name,
)
def test_no_font_import_inside_extra_head(path):
    """`@import` inside `extra_head` lands mid-<style> and is silently dropped.

    This is not a style preference -- the font simply never loads. Templates that
    need an extra face must use `{% block head_extra %}` with a real <link>.
    """
    text = path.read_text(encoding='utf-8')
    if '{% block extra_head %}' not in text:
        return
    start = text.index('{% block extra_head %}')
    # Find this block's matching endblock (templates use one extra_head block).
    end = text.find('{% endblock %}', start)
    body = text[start:end if end != -1 else len(text)]
    bad = re.findall(r'@import\s+url\([^)]*fonts\.googleapis[^)]*\)', body)
    assert not bad, (
        f"{path.name} has a font @import inside {{% block extra_head %}}. base.html "
        f"injects that block INSIDE its <style>, after other rules, so the @import is "
        f"invalid CSS and the font is never requested. Move it to "
        f"{{% block head_extra %}} as a real <link> (see tracker.html / chronicle_base.html)."
    )


def test_design_tokens_exist():
    """The tokens every template is told to use must actually be defined."""
    css = SYSTEM_CSS.read_text(encoding='utf-8')
    for token in ('--font-display', '--font-ui', '--font-flavor'):
        assert re.search(rf'{token}\s*:', css), f"{token} is not defined in system.css"


def test_mono_token_is_aliased_not_a_coding_font():
    """The coding font was deliberately retired; --font-mono stays only as an
    alias so old references resolve to the sans."""
    css = SYSTEM_CSS.read_text(encoding='utf-8')
    m = re.search(r'--font-mono\s*:\s*([^;]+);', css)
    assert m, '--font-mono should remain defined as an alias'
    assert 'var(--font-ui)' in m.group(1), (
        f"--font-mono must alias var(--font-ui), got: {m.group(1).strip()!r}. "
        f"Re-introducing a monospace face brings back the 'developer tool' look."
    )
