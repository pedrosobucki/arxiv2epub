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


TITLE_SIZES = (104, 92, 80, 70, 62, 54)
BODY_SIZE = 56
SMALL_SIZE = 44


def _block_height(lines: list[str], leading: int) -> int:
    return len(lines) * leading


def render(metadata: PaperMetadata) -> bytes | None:
    """Draw a cover JPEG, or return None when no usable font is installed."""
    body_font = _load_font(_SERIF_CANDIDATES, BODY_SIZE)
    small_font = _load_font(_SERIF_CANDIDATES, SMALL_SIZE)
    if not (body_font and small_font and _load_font(_SERIF_BOLD_CANDIDATES, 64)):
        log.warning("no serif font available; skipping cover generation")
        return None

    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    text_width = WIDTH - 2 * MARGIN

    # Masthead: a rule, then the identifier, so a shelf of these is scannable.
    draw.rectangle((MARGIN, MARGIN, MARGIN + 180, MARGIN + 12), fill="black")
    draw.text((MARGIN, MARGIN + 120), "arXiv", font=small_font, fill="black")
    draw.text(
        (MARGIN, MARGIN + 180), metadata.arxiv_id.versioned, font=small_font, fill="black"
    )

    authors = metadata.authors[:6]
    if len(metadata.authors) > 6:
        authors.append("and others")
    author_lines = _wrap(", ".join(authors), body_font, text_width)
    author_leading = int(BODY_SIZE * 1.32)

    band_top = MARGIN + 300
    band_bottom = HEIGHT - MARGIN - 160
    gap = 70

    # Shrink the title until the whole block fits the band. A long title at a
    # fixed size would otherwise run over the footer rule.
    for size in TITLE_SIZES:
        title_font = _load_font(_SERIF_BOLD_CANDIDATES, size)
        if title_font is None:
            continue
        title_lines = _wrap(metadata.title, title_font, text_width)
        title_leading = int(size * 1.22)
        height = (
            _block_height(title_lines, title_leading)
            + gap
            + _block_height(author_lines, author_leading)
        )
        if height <= band_bottom - band_top:
            break

    y = band_top + max(0, (band_bottom - band_top - height)) // 2
    for line in title_lines:
        draw.text((MARGIN, y), line, font=title_font, fill="black")
        y += title_leading

    y += gap
    for line in author_lines:
        draw.text((MARGIN, y), line, font=body_font, fill="black")
        y += author_leading

    draw.rectangle(
        (MARGIN, HEIGHT - MARGIN - 100, WIDTH - MARGIN, HEIGHT - MARGIN - 96),
        fill="black",
    )
    footer = " \u00b7 ".join(
        part for part in (metadata.primary_category, metadata.year) if part
    )
    if footer:
        draw.text((MARGIN, HEIGHT - MARGIN - 60), footer, font=small_font, fill="black")

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88, optimize=True, progressive=True)
    return buffer.getvalue()


__all__ = ["render"]
