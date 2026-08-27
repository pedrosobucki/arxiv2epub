from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from arxiv2epub.mathrender import MathRenderer, clean_mathml

LATEXML_MATH = """
<math alttext="d_{k}" class="ltx_Math" display="inline" id="S1.m1">
  <semantics>
    <msub><mi>d</mi><mi>k</mi></msub>
    <annotation encoding="application/x-tex">d_{k}</annotation>
  </semantics>
</math>
"""


def _math(markup: str = LATEXML_MATH):
    return BeautifulSoup(markup, "xml").find("math")


def test_latexml_wrappers_are_removed() -> None:
    cleaned = clean_mathml(_math())
    assert "annotation" not in cleaned
    assert "semantics" not in cleaned
    assert "alttext" not in cleaned
    assert "<msub>" in cleaned


def test_rendering_produces_a_measured_svg() -> None:
    rendered = MathRenderer().render(_math())
    assert rendered.media_type == "image/svg+xml"
    assert rendered.data.startswith(b"<svg")
    assert rendered.width_em > 0
    assert rendered.height_em > 0
    assert rendered.latex == "d_{k}"
    assert rendered.display is False


def test_a_subscript_hangs_below_the_baseline() -> None:
    # d_k descends; a lone "d" does not. The depth is what keeps inline maths
    # sitting on the text baseline rather than floating above it.
    with_subscript = MathRenderer().render(_math())
    without = MathRenderer().render(
        _math('<math alttext="d" display="inline"><mi>d</mi></math>')
    )
    assert with_subscript.depth_em > without.depth_em


def test_display_maths_is_marked_as_such() -> None:
    rendered = MathRenderer().render(
        _math('<math alttext="x" display="block"><mi>x</mi></math>')
    )
    assert rendered.display is True


def test_repeated_maths_is_only_drawn_once() -> None:
    renderer = MathRenderer()
    first = renderer.render(_math())
    second = renderer.render(_math())
    assert first is second
    assert len(renderer.cache) == 1


def test_inline_svg_follows_the_readers_text_colour() -> None:
    rendered = MathRenderer("inline-svg").render(_math())
    assert "currentColor" in rendered.svg_markup


def test_an_unknown_format_is_rejected_up_front() -> None:
    with pytest.raises(ValueError, match="unknown math format"):
        MathRenderer("pdf")
