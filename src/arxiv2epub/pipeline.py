"""Wiring the stages together: reference in, EPUB out."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import epub, ids, metadata as metadata_module, sources
from .http import Fetcher
from .mathrender import MathRenderer
from .models import Book
from .transform import transform

log = logging.getLogger(__name__)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


@dataclass
class Options:
    """Knobs the CLI exposes."""

    math_format: str = "svg"
    include_cover: bool = True
    download_images: bool = True
    cache_dir: Path | None = None
    timeout: float = 60.0


@dataclass
class Result:
    """What a conversion produced."""

    path: Path
    book: Book
    warnings: list[str] = field(default_factory=list)

    @property
    def size_bytes(self) -> int:
        return self.path.stat().st_size


def slugify(text: str, *, limit: int = 60) -> str:
    slug = _SLUG_STRIP.sub("-", text.lower()).strip("-")
    if len(slug) <= limit:
        return slug or "paper"
    # Cut on a word boundary so the name stays readable.
    return slug[:limit].rsplit("-", 1)[0] or slug[:limit]


def default_filename(meta: metadata_module.PaperMetadata) -> str:
    return f"{slugify(meta.title)}-{meta.arxiv_id.slug}.epub"


def build_epub(
    reference: str,
    output: Path | str | None = None,
    options: Options | None = None,
) -> Result:
    """Convert one arXiv reference into an EPUB.

    ``output`` may be a file path or a directory; when it is a directory (or
    omitted) the filename is derived from the paper's title and id.
    """
    options = options or Options()
    arxiv_id = ids.parse(reference)
    fetcher = Fetcher(cache_dir=options.cache_dir, timeout=options.timeout)

    log.info("looking up %s", arxiv_id.bare)
    meta = metadata_module.fetch(arxiv_id, fetcher)
    log.info("found %r by %s", meta.title, meta.author_line)

    source = sources.resolve(meta.arxiv_id, fetcher)
    math = MathRenderer(output_format=options.math_format)
    book = transform(
        source, meta, fetcher, math, download_images=options.download_images
    )

    if math.failures:
        log.warning("%d equation(s) could not be drawn", len(math.failures))

    destination = Path(output) if output else Path.cwd()
    if destination.is_dir() or not destination.suffix:
        destination = destination / default_filename(meta)

    epub.write(book, destination, include_cover=options.include_cover)
    log.info("wrote %s", destination)
    return Result(path=destination, book=book, warnings=book.warnings)
