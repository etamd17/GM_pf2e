"""Uploaded pictures: PC portraits and GM art for tracker combatants.

Every upload is decoded and RE-ENCODED with Pillow (never stored as sent),
shrunk, and written as WebP under ``campaigns/<cid>/dnd5e/images/`` with a
random name. Only names matching NAME_RE are ever turned into paths, so a
stored or requested name can't walk out of that directory.

Rules art from 5etools is a different thing: static files committed by
tools/dnd5e/fetch_images.py and looked up through content.image_for.
"""
from __future__ import annotations

import io
import os
import re
import uuid

from core import storage

NAME_RE = re.compile(r'^[0-9a-f]{32}\.webp$')
MAX_BYTES = 10_000_000
MAX_PIXELS = 40_000_000          # well under Pillow's bomb limit; a phone photo is ~12 MP
SIZE = 512
FORMATS = ('PNG', 'JPEG', 'WEBP', 'GIF')


class ImageError(ValueError):
    pass


def images_dir(cid: str) -> str:
    return os.path.join(storage.dnd5e_dir(cid), 'images')


def url_for_name(name: str | None) -> str | None:
    return f'/dnd5e/img/{name}' if name and NAME_RE.match(name) else None


def path_for(cid: str, name: str) -> str | None:
    if not name or not NAME_RE.match(name):
        return None
    p = os.path.join(images_dir(cid), name)
    return p if os.path.isfile(p) else None


def save_upload(cid: str, data: bytes, *, size: int = SIZE) -> str:
    """Validate + re-encode an uploaded picture. Returns the stored name."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    if not data:
        raise ImageError('Choose a picture to upload.')
    if len(data) > MAX_BYTES:
        raise ImageError('That picture is too large (10 MB at most).')
    try:
        im = Image.open(io.BytesIO(data))
        if im.format not in FORMATS:
            raise ImageError('Upload a PNG, JPEG, WebP or GIF.')
        w, h = im.size
        if w * h > MAX_PIXELS or w < 1 or h < 1:
            raise ImageError('That picture is too large.')
        im.load()
        im = ImageOps.exif_transpose(im)
        if im.mode not in ('RGB', 'RGBA'):
            im = im.convert('RGBA' if ('A' in im.getbands() or im.mode == 'P') else 'RGB')
        im.thumbnail((size, size), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, 'WEBP', quality=82, method=4)
    except ImageError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError):
        raise ImageError('That file is not a picture we can read.') from None
    except Exception:            # noqa: BLE001 -- malformed EXIF/palette data raises odd types; never a 500
        raise ImageError('That file is not a picture we can read.') from None
    name = uuid.uuid4().hex + '.webp'
    d = images_dir(cid)
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, name)
    tmp = dest + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(out.getvalue())
    os.replace(tmp, dest)
    return name


def delete(cid: str, name: str | None) -> bool:
    p = path_for(cid, name or '')
    if p is None:
        return False
    try:
        os.remove(p)
        return True
    except OSError:
        return False
