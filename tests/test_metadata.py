from __future__ import annotations

import pytest

from arxiv2epub.ids import ArxivId
from arxiv2epub.metadata import fetch
from conftest import StubFetcher

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <published>2017-06-12T18:57:34Z</published>
    <updated>2023-08-02T00:41:18Z</updated>
    <title>Attention Is All
  You Need</title>
    <summary>  The dominant sequence
transduction models are based on...
</summary>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <category term="cs.CL"/>
    <category term="cs.LG"/>
    <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="cs.CL"/>
    <arxiv:comment xmlns:arxiv="http://arxiv.org/schemas/atom">15 pages</arxiv:comment>
  </entry>
</feed>
"""

URL = "https://export.arxiv.org/api/query?id_list=1706.03762&max_results=1"


def _fetch(atom: str = ATOM, arxiv_id: ArxivId | None = None):
    fetcher = StubFetcher({URL: atom.encode("utf-8")})
    return fetch(arxiv_id or ArxivId("1706.03762"), fetcher)


def test_the_api_line_wrapping_is_undone() -> None:
    meta = _fetch()
    assert meta.title == "Attention Is All You Need"
    assert meta.abstract.startswith("The dominant sequence transduction models")


def test_an_unpinned_reference_picks_up_the_latest_version() -> None:
    assert _fetch().arxiv_id.versioned == "1706.03762v7"


def test_a_pinned_version_is_respected() -> None:
    meta = _fetch(arxiv_id=ArxivId("1706.03762", 3))
    assert meta.arxiv_id.versioned == "1706.03762v3"


def test_all_the_bibliographic_fields_come_through() -> None:
    meta = _fetch()
    assert meta.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert meta.categories == ["cs.CL", "cs.LG"]
    assert meta.primary_category == "cs.CL"
    assert meta.comment == "15 pages"
    assert meta.year == "2017"


def test_long_author_lists_are_abbreviated_for_display() -> None:
    meta = _fetch()
    assert meta.author_line == "Ashish Vaswani, Noam Shazeer"
    meta.authors = [f"Author {n}" for n in range(9)]
    assert meta.author_line == "Author 0 et al."


def test_an_unknown_paper_is_an_error_not_an_empty_book() -> None:
    empty = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    with pytest.raises(LookupError):
        _fetch(empty)
