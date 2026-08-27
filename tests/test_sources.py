from __future__ import annotations

import pytest

from arxiv2epub.ids import ArxivId
from arxiv2epub.sources import NoHtmlAvailable, candidate_urls, looks_like_a_paper, resolve
from conftest import StubFetcher

PAPER = (
    '<html><body><article class="ltx_document">'
    + "<p>Real paper content. </p>" * 200
    + "</article></body></html>"
)
STUB = '<html><body><article class="ltx_document"><p>No HTML available.</p></article></body></html>'


def test_a_pinned_version_is_tried_before_the_bare_id() -> None:
    urls = [url for _, url in candidate_urls(ArxivId("1706.03762", 7))]
    assert urls[0] == "https://arxiv.org/html/1706.03762v7"
    assert urls[-1].startswith("https://ar5iv")


def test_arxiv_is_preferred_over_ar5iv() -> None:
    providers = [provider for provider, _ in candidate_urls(ArxivId("1706.03762"))]
    assert providers.index("arxiv") < providers.index("ar5iv")


def test_a_conversion_stub_is_not_mistaken_for_a_paper() -> None:
    assert looks_like_a_paper(PAPER)
    assert not looks_like_a_paper(STUB)
    assert not looks_like_a_paper("<html><body>nothing here</body></html>")


def test_ar5iv_is_used_when_arxiv_has_no_html() -> None:
    fetcher = StubFetcher(
        {"https://ar5iv.labs.arxiv.org/html/1706.03762": PAPER.encode("utf-8")}
    )
    source = resolve(ArxivId("1706.03762"), fetcher)
    assert source.provider == "ar5iv"


def test_an_empty_rendering_falls_through_to_the_next_provider() -> None:
    fetcher = StubFetcher(
        {
            "https://arxiv.org/html/1706.03762": STUB.encode("utf-8"),
            "https://ar5iv.labs.arxiv.org/html/1706.03762": PAPER.encode("utf-8"),
        }
    )
    assert resolve(ArxivId("1706.03762"), fetcher).provider == "ar5iv"


def test_exhausting_every_provider_says_what_was_tried() -> None:
    with pytest.raises(NoHtmlAvailable, match="ar5iv"):
        resolve(ArxivId("1706.03762"), StubFetcher())


def test_a_provider_can_be_forced() -> None:
    only = candidate_urls(ArxivId("1706.03762", 7), "ar5iv")
    assert [provider for provider, _ in only] == ["ar5iv"]


def test_an_unknown_provider_is_rejected_up_front() -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        candidate_urls(ArxivId("1706.03762"), "pdf")


def test_the_failure_says_where_to_find_the_paper() -> None:
    with pytest.raises(NoHtmlAvailable, match="arxiv.org/abs/1706.03762"):
        resolve(ArxivId("1706.03762"), StubFetcher())
