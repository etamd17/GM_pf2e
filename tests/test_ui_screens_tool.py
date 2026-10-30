"""tools/ui_screens.py compare: the before/after screenshot check (DS-01)."""
import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from tools.ui_screens import _route_problem, compare  # noqa: E402


def _png(path, size=(20, 10), color=(12, 10, 7), dot=None):
    img = Image.new("RGB", size, color)
    if dot:
        img.putpixel(dot, (255, 255, 255))
    img.save(path)


def test_nothing_to_compare_is_a_failure_not_a_pass(tmp_path, capsys):
    assert compare(tmp_path / "nope1", tmp_path / "nope2") == 1
    # The wrong folder level: the images are one directory down.
    (tmp_path / "before" / "pf2e").mkdir(parents=True), (tmp_path / "after" / "pf2e").mkdir(parents=True)
    _png(tmp_path / "before" / "pf2e" / "x.png"), _png(tmp_path / "after" / "pf2e" / "x.png", dot=(1, 1))
    assert compare(tmp_path / "before", tmp_path / "after") == 1
    assert "NOTHING to compare" in capsys.readouterr().out


@pytest.mark.parametrize(("status", "url", "route", "who", "bad"), [
    (200, "http://h/gm", "/gm", "gm", False),
    (200, "http://h/me", "/", "gm", False),
    (200, "http://h/login", "/login", "gm", False),
    (200, "http://h/login", "/", "anon", False),
    (503, "http://h/dnd5e/gm", "/dnd5e/gm", "gm", True),
    (500, "http://h/x", "/x", "anon", True),
    (None, "http://h/x", "/x", "gm", True),
    (200, "http://h/login?next=/gm", "/gm", "gm", True),
])
def test_an_error_page_or_a_login_bounce_is_not_a_screenshot(status, url, route, who, bad):
    assert bool(_route_problem(status, url, route, who)) is bad


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
