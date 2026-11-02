"""The focus gate rejects invisible rings rather than trusting outline-style alone."""
import importlib
import importlib.util
from contextlib import contextmanager

import pytest

from tools import ui_screens


def _tool():
    assert callable(getattr(ui_screens, "focus", None)), "the focus command is missing"
    assert importlib.util.find_spec("tools.ui_focus") is not None
    return importlib.import_module("tools.ui_focus")


@pytest.mark.parametrize("change", [
    {"outlineStyle": "none"}, {"outlineWidth": 0}, {"outlineAlpha": 0},
    {"effectiveOpacity": 0}, {"contrast": 2.99}, {"contrast": None},
])
def test_an_invisible_or_unmeasured_ring_fails(change):
    row = dict(outlineStyle="solid", outlineWidth=2, outlineAlpha=1,
               effectiveOpacity=1, contrast=8)
    row.update(change)
    assert _tool().problems(row)


def test_a_dim_control_can_pass_when_its_painted_ring_has_enough_contrast():
    assert _tool().problems(dict(outlineStyle="solid", outlineWidth=2,
        outlineAlpha=1, effectiveOpacity=.55, contrast=3.66)) == []


def test_contrast_uses_linearized_srgb():
    tool = _tool()
    assert tool.contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21)
    assert tool.contrast_ratio((123, 87, 13), (123, 87, 13)) == 1


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_focus_report_refuses_non_finite_measurements(monkeypatch, tmp_path, value):
    @contextmanager
    def served(*args):
        yield 0, None, []

    monkeypatch.setattr(ui_screens, '_served', served)
    monkeypatch.setattr(ui_screens, '_pages', lambda *args: iter([
        ('test', 'gm', '/synthetic', '/synthetic', object())]))
    monkeypatch.setattr(_tool(), 'audit_states', lambda *args: ([{'measurement': value}], []))
    out = tmp_path / 'evidence'
    with pytest.raises(ValueError, match='Out of range float values'):
        ui_screens.focus(tmp_path, 'pf2e', out)
    assert not (out / 'focus.json').exists()
