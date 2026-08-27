"""Paper metadata, taken from the arXiv Atom API."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from xml.etree import ElementTree

from .http import Fetcher
from .ids import ArxivId

log = logging.getLogger(__name__)

API_URL = "https://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"


@dataclass
class PaperMetadata:
    """Bibliographic details for one paper."""

    arxiv_id: ArxivId
    title: str
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    published: date | None = None
    updated: date | None = None
    categories: list[str] = field(default_factory=list)
    primary_category: str = ""
    doi: str = ""
    journal_ref: str = ""
    comment: str = ""

    @property
    def author_line(self) -> str:
        """Authors as one readable string, abbreviated for long author lists."""
        if not self.authors:
            return "Unknown author"
        if len(self.authors) <= 4:
            return ", ".join(self.authors)
        return f"{self.authors[0]} et al."

    @property
    def year(self) -> str:
        stamp = self.published or self.updated
        return str(stamp.year) if stamp else ""


def _clean(text: str | None) -> str:
    """Collapse the newlines and padding the arXiv API puts in text fields."""
    return re.sub(r"\s+", " ", (text or "")).strip()


def _parse_date(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def fetch(arxiv_id: ArxivId, fetcher: Fetcher) -> PaperMetadata:
    """Look up a paper in the arXiv API.

    The API always describes the newest version, so the returned metadata also
    tells us which version number to request HTML for when the caller did not
    pin one.
    """
    url = f"{API_URL}?id_list={arxiv_id.bare}&max_results=1"
    raw = fetcher.get_text(url)
    root = ElementTree.fromstring(raw)
    entry = root.find(f"{_ATOM}entry")
    if entry is None:
        raise LookupError(f"arXiv has no record of {arxiv_id.bare}")

    title = _clean(entry.findtext(f"{_ATOM}title"))
    if not title:
        raise LookupError(f"arXiv returned no title for {arxiv_id.bare}")

    authors = [
        _clean(node.findtext(f"{_ATOM}name"))
        for node in entry.findall(f"{_ATOM}author")
        if _clean(node.findtext(f"{_ATOM}name"))
    ]
    categories = [
        term
        for node in entry.findall(f"{_ATOM}category")
        if (term := node.get("term"))
    ]
    primary = entry.find(f"{_ARXIV}primary_category")

    # The <id> element carries the latest version, e.g. .../abs/1706.03762v7.
    latest_version = arxiv_id.version
    id_text = entry.findtext(f"{_ATOM}id") or ""
    if match := re.search(r"v(\d+)\s*$", id_text):
        latest_version = arxiv_id.version or int(match.group(1))

    return PaperMetadata(
        arxiv_id=arxiv_id.with_version(latest_version),
        title=title,
        authors=authors,
        abstract=_clean(entry.findtext(f"{_ATOM}summary")),
        published=_parse_date(entry.findtext(f"{_ATOM}published")),
        updated=_parse_date(entry.findtext(f"{_ATOM}updated")),
        categories=categories,
        primary_category=(primary.get("term") if primary is not None else "") or "",
        doi=_clean(entry.findtext(f"{_ARXIV}doi")),
        journal_ref=_clean(entry.findtext(f"{_ARXIV}journal_ref")),
        comment=_clean(entry.findtext(f"{_ARXIV}comment")),
    )
