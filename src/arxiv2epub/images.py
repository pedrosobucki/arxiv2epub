"""Getting figures into a shape a Kindle is happy with.

E-ink screens top out around 1236-1860 pixels wide, so full-resolution figures
are pure file size. Diagrams stay PNG because flat colour compresses better and
JPEG artefacts around thin lines are very visible; photographs and dense plots
become JPEG.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

log = logging.getLogger(__name__)

Image.MAX_IMAGE_PIXELS = 200_000_000

MAX_WIDTH = 1600
MAX_HEIGHT = 2200
JPEG_QUALITY = 82

# Formats an EPUB reader must support, which therefore need no conversion.
PASSTHROUGH_MEDIA_TYPES = {"image/svg+xml", "image/gif"}

_EXTENSION_BY_MEDIA_TYPE = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/svg+xml": "svg",
}


@dataclass
class PreparedImage:
    data: bytes
    media_type: str

    @property
    def extension(self) -> str:
        return _EXTENSION_BY_MEDIA_TYPE.get(self.media_type, "img")


def guess_media_type(url: str, data: bytes) -> str:
    """Identify an image from its magic bytes, falling back to its extension."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    head = data[:512].lstrip()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:2048]):
        return "image/svg+xml"

    suffix = url.rsplit(".", 1)[-1].lower().split("?")[0]
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "svg": "image/svg+xml",
    }.get(suffix, "application/octet-stream")


def _looks_like_a_diagram(image: Image.Image) -> bool:
    """Flat-colour line art keeps its edges crisp only if it stays lossless."""
    if image.mode in ("P", "1", "L", "LA", "RGBA"):
        return True
    return image.getcolors(maxcolors=4096) is not None


def prepare(data: bytes, url: str, *, max_width: int = MAX_WIDTH) -> PreparedImage:
    """Downscale and re-encode one figure, or pass it through untouched."""
    media_type = guess_media_type(url, data)
    if media_type in PASSTHROUGH_MEDIA_TYPES:
        return PreparedImage(data, media_type)

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        log.debug("leaving %s untouched: %s", url, exc)
        return PreparedImage(data, media_type)

    if image.width > max_width or image.height > MAX_HEIGHT:
        image.thumbnail((max_width, MAX_HEIGHT), Image.LANCZOS)

    diagram = _looks_like_a_diagram(image)
    buffer = io.BytesIO()
    if diagram:
        if image.mode not in ("P", "L", "LA", "RGB", "RGBA", "1"):
            image = image.convert("RGBA")
        image.save(buffer, format="PNG", optimize=True)
        out_type = "image/png"
    else:
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
        out_type = "image/jpeg"

    encoded = buffer.getvalue()
    # Re-encoding is only worth it when it actually saves space.
    if len(encoded) >= len(data) and media_type in _EXTENSION_BY_MEDIA_TYPE:
        return PreparedImage(data, media_type)
    return PreparedImage(encoded, out_type)
