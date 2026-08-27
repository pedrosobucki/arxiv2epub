"""Locating a LaTeXML-rendered HTML version of a paper.

arXiv publishes native HTML for most recent submissions at ``/html/<id>v<n>``.
Anything it does not cover is usually available from ar5iv, which ran the same
LaTeXML pipeline over the older backlog. Both emit the same ``ltx_*`` markup, so
one parser handles either.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .http import Fetcher
from .ids import ArxivId

log = logging.getLogger(__name__)

# A LaTeXML run that failed still returns a page; real papers are far larger.
_MIN_CONTENT_CHARS = 2000


class NoHtmlAvailable(RuntimeError):
    """Raised when neither arXiv nor ar5iv can supply HTML for a paper."""


@dataclass
class HtmlSource:
    """A fetched HTML rendering of a paper."""

    url: str
    html: str
    provider: str

    @property
    def base_url(self) -> str:
        """The URL relative asset paths in this document resolve against."""
        return urljoin(self.url, ".")


def candidate_urls(arxiv_id: ArxivId) -> list[tuple[str, str]]:
    """Return ``(provider, url)`` pairs to try, best rendering first."""
    candidates: list[tuple[str, str]] = []
    if arxiv_id.version:
        candidates.append(("arxiv", f"https://arxiv.org/html/{arxiv_id.versioned}"))
    candidates.append(("arxiv", f"https://arxiv.org/html/{arxiv_id.bare}"))
    candidates.append(("ar5iv", f"https://ar5iv.labs.arxiv.org/html/{arxiv_id.bare}"))
    # Drop duplicates while preserving order.
    seen: set[str] = set()
    return [c for c in candidates if not (c[1] in seen or seen.add(c[1]))]


def looks_like_a_paper(html: str) -> bool:
    """Reject stub pages that carry LaTeXML chrome but no actual paper."""
    soup = BeautifulSoup(html, "lxml")
    article = soup.find("article", class_="ltx_document") or soup.find(
        "div", class_="ltx_page_content"
    )
    if article is None:
        return False
    return len(article.get_text(" ", strip=True)) >= _MIN_CONTENT_CHARS


def resolve(arxiv_id: ArxivId, fetcher: Fetcher) -> HtmlSource:
    """Fetch the best available HTML rendering of a paper."""
    attempts: list[str] = []
    for provider, url in candidate_urls(arxiv_id):
        try:
            body, resolved_url = fetcher.get(url)
        except Exception as exc:  # noqa: BLE001 - any failure means "try the next one"
            log.debug("%s unavailable: %s", url, exc)
            attempts.append(f"{url} (unreachable)")
            continue
        html = body.decode("utf-8", errors="replace")
        if not looks_like_a_paper(html):
            attempts.append(f"{url} (no paper content)")
            continue
        log.info("using %s rendering at %s", provider, resolved_url)
        return HtmlSource(url=resolved_url, html=html, provider=provider)

    raise NoHtmlAvailable(
        f"no HTML rendering of {arxiv_id.bare} could be fetched; tried: "
        + "; ".join(attempts)
    )
