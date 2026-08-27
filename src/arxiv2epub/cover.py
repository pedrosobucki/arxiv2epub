"""A generated cover, so the book has a recognisable spine in the library.

Kindle shows the cover in the home grid, and a paper without one shows up as a
grey placeholder, which makes a shelf of converted papers unusable.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .metadata import PaperMetadata

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 1600, 2560
MARGIN = 150

_SERIF_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSerif.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
)
_SERIF_BOLD_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSerif-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
)


def _load_font(candidates: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont | None:
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:  # pragma: no cover - unreadable font file
                continue
    return None


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Greedily wrap text to a pixel width using the font's own metrics."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if font.getlength(candidate) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def render(metadata: PaperMetadata) -> bytes | None:
    """Draw a cover PNG, or return None when no usable font is installed."""
    title_font = _load_font(_SERIF_BOLD_CANDIDATES, 96)
    body_font = _load_font(_SERIF_CANDIDATES, 56)
    small_font = _load_font(_SERIF_CANDIDATES, 44)
    if not (title_font and body_font and small_font):
        log.warning("no serif font available; skipping cover generation")
        return None

    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    text_width = WIDTH - 2 * MARGIN

    draw.rectangle((MARGIN, MARGIN, MARGIN + 180, MARGIN + 12), fill="black")

    y = MARGIN + 120
    draw.text((MARGIN, y), "arXiv", font=small_font, fill="black")
    y += 60
    draw.text((MARGIN, y), metadata.arxiv_id.versioned, font=small_font, fill="black")

    y = int(HEIGHT * 0.32)
    for line in _wrap(metadata.title, title_font, text_width):
        draw.text((MARGIN, y), line, font=title_font, fill="black")
        y += 118

    y += 70
    authors = metadata.authors[:6]
    if len(metadata.authors) > 6:
        authors.append("and others")
    for line in _wrap(", ".join(authors), body_font, text_width):
        draw.text((MARGIN, y), line, font=body_font, fill="black")
        y += 74

    footer = " · ".join(
        part
        for part in (metadata.primary_category, metadata.year)
        if part
    )
    if footer:
        draw.text(
            (MARGIN, HEIGHT - MARGIN - 60), footer, font=small_font, fill="black"
        )
    draw.rectangle(
        (MARGIN, HEIGHT - MARGIN - 100, WIDTH - MARGIN, HEIGHT - MARGIN - 96),
        fill="black",
    )

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88, optimize=True, progressive=True)
    return buffer.getvalue()


__all__ = ["render"]
