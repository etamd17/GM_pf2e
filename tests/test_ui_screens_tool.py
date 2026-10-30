"""tools/ui_screens.py compare: the before/after screenshot check (DS-01)."""
import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from tools.ui_screens import compare  # noqa: E402


def _png(path, size=(20, 10), color=(12, 10, 7), dot=None):
    img = Image.new("RGB", size, color)
    if dot:
        img.putpixel(dot, (255, 255, 255))
    img.save(path)


def test_identical_screenshots_pass(tmp_path, capsys):
    (tmp_path / "a").mkdir(), (tmp_path / "b").mkdir()
    _png(tmp_path / "a" / "x.png"), _png(tmp_path / "b" / "x.png")
    assert compare(tmp_path / "a", tmp_path / "b") == 0
    assert "same     x.png" in capsys.readouterr().out


def test_one_changed_pixel_size_change_and_missing_image_fail(tmp_path, capsys):
    (tmp_path / "a").mkdir(), (tmp_path / "b").mkdir()
    _png(tmp_path / "a" / "dot.png"), _png(tmp_path / "b" / "dot.png", dot=(3, 4))
    _png(tmp_path / "a" / "tall.png"), _png(tmp_path / "b" / "tall.png", size=(20, 11))
    _png(tmp_path / "a" / "gone.png")
    assert compare(tmp_path / "a", tmp_path / "b", tmp_path / "diff") == 1
    out = capsys.readouterr().out
    assert "CHANGED  dot.png: 1 px" in out and "SIZE     tall.png" in out and "MISSING  gone.png" in out
    assert (tmp_path / "diff" / "dot.png").exists()


def test_a_one_level_difference_in_one_channel_still_counts(tmp_path, capsys):
    (tmp_path / "a").mkdir(), (tmp_path / "b").mkdir()
    _png(tmp_path / "a" / "x.png", color=(12, 10, 7))
    _png(tmp_path / "b" / "x.png", color=(12, 10, 8))
    assert compare(tmp_path / "a", tmp_path / "b") == 1
    assert "CHANGED  x.png: 200 px" in capsys.readouterr().out
