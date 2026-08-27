from __future__ import annotations

import io

from PIL import Image

from arxiv2epub import cover


def _render(metadata) -> Image.Image:
    data = cover.render(metadata)
    assert data is not None, "no serif font available in this environment"
    return Image.open(io.BytesIO(data))


def test_the_cover_is_a_kindle_shaped_jpeg(paper_metadata) -> None:
    image = _render(paper_metadata)
    assert image.size == (cover.WIDTH, cover.HEIGHT)
    assert image.format == "JPEG"


def test_a_long_title_and_a_long_author_list_stay_inside_the_page(
    paper_metadata,
) -> None:
    paper_metadata.title = (
        "A Very Long Title Indeed About Scaling Laws for Neural Language "
        "Models and Their Emergent Capabilities Under Extreme Compute Budgets"
    )
    paper_metadata.authors = [f"Author Number {n}" for n in range(20)]
    image = _render(paper_metadata).convert("L")

    # Nothing may be drawn over the footer rule at the bottom of the page.
    footer = image.crop(
        (0, cover.HEIGHT - cover.MARGIN - 90, cover.WIDTH, cover.HEIGHT - cover.MARGIN - 70)
    )
    assert footer.getextrema()[0] > 200, "text collided with the footer"


def test_a_missing_font_is_reported_rather_than_crashing(monkeypatch, paper_metadata) -> None:
    monkeypatch.setattr(cover, "_load_font", lambda *args: None)
    assert cover.render(paper_metadata) is None
