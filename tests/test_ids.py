from __future__ import annotations

import pytest

from arxiv2epub.ids import ArxivId, NotAnArxivReference, parse


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("1706.03762", "1706.03762"),
        ("arXiv:1706.03762v7", "1706.03762v7"),
        ("https://arxiv.org/abs/2301.08243", "2301.08243"),
        ("https://arxiv.org/abs/2301.08243v3", "2301.08243v3"),
        ("https://arxiv.org/pdf/2501.12948v1.pdf", "2501.12948v1"),
        ("https://ar5iv.labs.arxiv.org/html/1706.03762", "1706.03762"),
        ("https://doi.org/10.48550/arXiv.2501.12948", "2501.12948"),
        ("  2501.12948  ", "2501.12948"),
        ("hep-th/9901001v2", "hep-th/9901001v2"),
        ("math.GT/0309136", "math.GT/0309136"),
    ],
)
def test_parses_the_shapes_people_paste(reference: str, expected: str) -> None:
    assert parse(reference).versioned == expected


@pytest.mark.parametrize("reference", ["", "   ", "not a paper", "https://example.com"])
def test_rejects_anything_without_an_id(reference: str) -> None:
    with pytest.raises(NotAnArxivReference):
        parse(reference)


def test_a_legacy_id_is_safe_to_use_as_a_filename() -> None:
    assert parse("hep-th/9901001v2").slug == "hep-th_9901001v2"


def test_version_can_be_replaced_without_touching_the_number() -> None:
    assert ArxivId("1706.03762").with_version(7).versioned == "1706.03762v7"
