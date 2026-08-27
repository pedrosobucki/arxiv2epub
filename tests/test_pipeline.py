from __future__ import annotations

import pytest

from arxiv2epub.pipeline import default_filename, slugify


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Attention Is All You Need", "attention-is-all-you-need"),
        ("DeepSeek-R1: Incentivizing Reasoning", "deepseek-r1-incentivizing-reasoning"),
        ("  Spaces   &   Symbols!  ", "spaces-symbols"),
        ("", "paper"),
    ],
)
def test_titles_become_readable_slugs(title: str, expected: str) -> None:
    assert slugify(title) == expected


def test_a_long_title_is_cut_on_a_word_boundary() -> None:
    slug = slugify(
        "Self-Supervised Learning from Images with a Joint-Embedding "
        "Predictive Architecture"
    )
    assert len(slug) <= 60
    assert not slug.endswith("-")
    assert slug.split("-")[-1] in {"joint", "embedding", "a", "with"}


def test_the_filename_pins_the_version_that_was_converted(paper_metadata) -> None:
    assert default_filename(paper_metadata) == (
        "attention-is-all-you-need-1706.03762v7.epub"
    )
