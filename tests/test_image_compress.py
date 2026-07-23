"""Guards for _save_image_compressed — the upload downscale/recompress that
bounds portrait/handout/hero/crest growth on the Railway volume.

Contract: large images are downscaled to the cap (preserving aspect), small
images are left alone (never upscaled), and anything Pillow can't safely process
(animated frames, non-images) falls back to saving the ORIGINAL bytes so an
upload is never lost."""
from __future__ import annotations

import io
import os

import pytest

import app

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402
from werkzeug.datastructures import FileStorage  # noqa: E402


def _img_filestorage(w, h, fmt, filename):
    img = Image.new('RGB', (w, h), (120, 60, 30))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    buf.seek(0)
    return FileStorage(stream=buf, filename=filename)


def test_large_jpeg_downscaled_to_cap(tmp_path):
    fs = _img_filestorage(4000, 3000, 'JPEG', 'big.jpg')
    out = tmp_path / 'out.jpg'
    n = app._save_image_compressed(fs, str(out), max_dim=768)
    assert out.exists()
    w, h = Image.open(out).size
    assert max(w, h) <= 768
    assert h < w  # aspect preserved (was 4:3 landscape)
    assert n == os.path.getsize(out)


def test_small_image_not_upscaled(tmp_path):
    fs = _img_filestorage(200, 100, 'PNG', 'small.png')
    out = tmp_path / 'out.png'
    app._save_image_compressed(fs, str(out), max_dim=768)
    assert Image.open(out).size == (200, 100)


def test_animated_gif_preserved(tmp_path):
    # Distinct colored frames so the fixture is genuinely multi-frame.
    frames = [Image.new('RGB', (300, 300), c).convert('P')
              for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    buf = io.BytesIO()
    frames[0].save(buf, format='GIF', save_all=True, append_images=frames[1:], duration=100)
    original = buf.getvalue()
    # Sanity: the fixture really is animated.
    assert getattr(Image.open(io.BytesIO(original)), 'n_frames', 1) == 3
    buf.seek(0)
    fs = FileStorage(stream=buf, filename='anim.gif')
    out = tmp_path / 'anim.gif'
    app._save_image_compressed(fs, str(out), max_dim=64)
    # Multi-frame image is saved untouched (bytes unchanged -> still animated).
    assert out.read_bytes() == original


def test_non_image_falls_back_to_original_bytes(tmp_path):
    data = b'this is definitely not an image'
    fs = FileStorage(stream=io.BytesIO(data), filename='x.png')
    out = tmp_path / 'x.png'
    app._save_image_compressed(fs, str(out), max_dim=512)
    assert out.read_bytes() == data
