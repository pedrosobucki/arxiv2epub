from __future__ import annotations

import io

from PIL import Image

from arxiv2epub.images import guess_media_type, prepare


def _encode(image: Image.Image, fmt: str) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def test_media_type_comes_from_the_bytes_not_the_name() -> None:
    png = _encode(Image.new("RGB", (4, 4), "white"), "PNG")
    assert guess_media_type("figure.jpg", png) == "image/png"
    assert guess_media_type("x", b"<svg xmlns='...'></svg>") == "image/svg+xml"


def test_oversized_figures_are_scaled_to_screen_size() -> None:
    original = Image.new("RGB", (4000, 3000))
    for x in range(0, 4000, 7):
        for y in range(0, 3000, 11):
            original.putpixel((x, y), (x % 256, y % 256, (x + y) % 256))

    prepared = prepare(_encode(original, "PNG"), "big.png")
    assert Image.open(io.BytesIO(prepared.data)).width <= 1600


def test_flat_line_art_stays_lossless() -> None:
    diagram = Image.new("RGB", (400, 300), "white")
    for x in range(400):
        diagram.putpixel((x, 150), (0, 0, 0))
    assert prepare(_encode(diagram, "PNG"), "diagram.png").media_type == "image/png"


def test_vector_figures_pass_through_untouched() -> None:
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="2" height="2"/></svg>'
    prepared = prepare(svg, "figure.svg")
    assert prepared.data == svg
    assert prepared.media_type == "image/svg+xml"
