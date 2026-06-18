"""Cosmere player-view theming completeness.

The Cosmere side must wear its own storm-blue identity, never gilt. Most surfaces
already read var(--accent) (re-pointed to --storm-300), but anything reading the
gilt ramp directly (var(--gilt-100) headings, --accent-gold, --accent-primary,
--color-spell, the shared focus glow) and a couple of baked-gilt flavor
flourishes leaked gold on the blue side.

Fix: remap the whole gilt ramp under body.system-cosmere so every gilt-derived
token + alias renders Stormlight in one place, plus override the crit-roll toast
flourish (which bakes gilt hex). Verified live: forcing system-cosmere on a
base-extending page flips var(--gilt-100) -> rgb(211,236,251) and
var(--gilt-300)/var(--accent)/var(--accent-primary) -> rgb(95,168,224).
"""
from __future__ import annotations

import os
import pathlib

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _css() -> str:
    return pathlib.Path(_REPO, 'static', 'css', 'system.css').read_text()


def _cosmere_token_block(css: str) -> str:
    """The `body.system-cosmere { ... }` token declaration block (flat, so the
    next '}' closes it)."""
    i = css.index('body.system-cosmere {')
    return css[i:css.index('}', i)]


def test_cosmere_remaps_the_whole_gilt_ramp():
    block = _cosmere_token_block(_css())
    for tok in ('--gilt-100:', '--gilt-200:', '--gilt-300:', '--gilt-400:',
                '--gilt-500:', '--gilt-600:'):
        assert tok in block, '%s not remapped under body.system-cosmere' % tok
    # The ramp is re-pointed to the Stormlight palette, not left gilt.
    assert '#5fa8e0' in block and '#9fd2f2' in block
    assert '#c9a34e' not in block and '#f0d88a' not in block, 'gilt hex still in the Cosmere block'


def test_cosmere_accent_and_glow_are_storm():
    block = _cosmere_token_block(_css())
    assert '--accent:' in block and 'var(--storm-300)' in block
    # The shared gilt focus glow is cooled to storm so it doesn't flash gold.
    assert '--shadow-glow-gilt:' in block and 'rgba(95,168,224' in block


def test_cosmere_crit_flourish_is_overridden():
    css = _css()
    assert 'body.system-cosmere .roll-toast.crit-flourish' in css, \
        'crit-roll toast still bakes gilt on the Cosmere side'
