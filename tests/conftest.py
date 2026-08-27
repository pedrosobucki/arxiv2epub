from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arxiv2epub.ids import ArxivId  # noqa: E402
from arxiv2epub.metadata import PaperMetadata  # noqa: E402
from arxiv2epub.sources import HtmlSource  # noqa: E402


class StubFetcher:
    """Serves canned bytes so the tests never touch the network."""

    def __init__(self, responses: dict[str, bytes] | None = None):
        self.responses = responses or {}
        self.requested: list[str] = []

    def get(self, url: str, *, allow_cache: bool = True) -> tuple[bytes, str]:
        self.requested.append(url)
        if url not in self.responses:
            raise FileNotFoundError(url)
        return self.responses[url], url

    def get_bytes(self, url: str, *, allow_cache: bool = True) -> bytes:
        return self.get(url)[0]

    def get_text(self, url: str, *, allow_cache: bool = True) -> str:
        return self.get_bytes(url).decode("utf-8")

    def try_get_bytes(self, url: str, *, allow_cache: bool = True) -> bytes | None:
        try:
            return self.get_bytes(url)
        except FileNotFoundError:
            return None


@pytest.fixture
def fetcher() -> StubFetcher:
    return StubFetcher()


@pytest.fixture
def paper_metadata() -> PaperMetadata:
    return PaperMetadata(
        arxiv_id=ArxivId("1706.03762", 7),
        title="Attention Is All You Need",
        authors=["Ashish Vaswani", "Noam Shazeer"],
        abstract="The dominant sequence transduction models...",
        published=date(2017, 6, 12),
        categories=["cs.CL", "cs.LG"],
        primary_category="cs.CL",
    )


def make_source(body: str, url: str = "https://arxiv.org/html/1706.03762v7") -> HtmlSource:
    html = f'<html><body><article class="ltx_document">{body}</article></body></html>'
    return HtmlSource(url=url, html=html, provider="arxiv")
