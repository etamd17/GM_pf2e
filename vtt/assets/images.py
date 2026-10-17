"""What counts as an acceptable image for the asset store (M6, handoff 053).

The type is decided by the bytes, never by what the client claimed: the magic
number picks PNG, JPEG or WebP, then Pillow must open the header and verify the
file. Dimensions come from the header without decoding the pixels, so a
decompression bomb is refused by its declared size before any work is done.

The bytes are stored as uploaded. Re-encoding would strip metadata (EXIF stays,
a stated limit) but would also make the content hash depend on the Pillow
version, and the id is that hash.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

MAX_BYTES = 25 * 1024 * 1024
MAX_DIMENSION = 16384
MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp")


class BadImage(ValueError):
    """Refused, with a reason fit to show the GM."""


@dataclass(frozen=True)
class ImageInfo:
    media_type: str
    width: int
    height: int


def sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def inspect(data: bytes) -> ImageInfo:
    if not data:
        raise BadImage("the file is empty")
    if len(data) > MAX_BYTES:
        raise BadImage("images must be 25 MB or smaller")
    media_type = sniff(data)
    if media_type is None:
        raise BadImage("upload a PNG, JPEG or WebP image")
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as probe:   # header only: no pixels decoded
            width, height = probe.size
            fmt = probe.format
        if not (1 <= width <= MAX_DIMENSION and 1 <= height <= MAX_DIMENSION):
            raise BadImage(f"images must be at most {MAX_DIMENSION} pixels on each side")
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
    except BadImage:
        raise
    except Exception as exc:  # any decoder complaint is an unreadable file
        raise BadImage("that file is not a readable PNG, JPEG or WebP image") from exc
    if {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(fmt or "") != media_type:
        raise BadImage("that file is not a readable PNG, JPEG or WebP image")
    return ImageInfo(media_type, int(width), int(height))
