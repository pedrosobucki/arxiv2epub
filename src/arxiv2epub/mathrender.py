"""Turning LaTeXML's MathML into images the Kindle can actually display.

Kindle's MathML support is unreliable, so every equation becomes a picture.
ziamath draws MathML with real glyph outlines from STIX Two Math, which means
the resulting SVG carries no font dependency of its own.

Each image is measured in ``em`` rather than pixels so that equations scale with
whatever font size the reader has chosen, and inline math is given a
``vertical-align`` so it sits on the surrounding text's baseline instead of
floating above it.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from xml.etree import ElementTree

import ziamath
from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)

# ziamath defaults to SVG 2 markup, which many e-readers do not understand.
ziamath.config.svg2 = False
ziamath.config.precision = 2

# Everything is drawn at this nominal pixel size and then expressed as a
# multiple of it, so the value itself never reaches the output.
_RENDER_SIZE = 24.0

# Padding ziamath leaves around the glyphs so nothing clips at the SVG edge.
# It has to come back out of the baseline offset, or inline maths sits low.
_RENDER_MARGIN = 1.0

_VIEWBOX = re.compile(r"\s+")

# Attributes LaTeXML adds for cross-referencing that ziamath does not understand.
_DROPPED_MATH_ATTRS = {"id", "class", "alttext", "intent", "xref", "arg", "data-semantic"}

MATH_FORMATS = ("svg", "inline-svg", "png")


class MathRenderError(RuntimeError):
    """Raised when an equation cannot be drawn by any available backend."""


@dataclass
class RenderedMath:
    """One drawn equation, ready to be placed in a chapter."""

    key: str
    data: bytes
    media_type: str
    width_em: float
    height_em: float
    depth_em: float
    latex: str
    display: bool
    svg_markup: str = ""

    @property
    def extension(self) -> str:
        return "png" if self.media_type == "image/png" else "svg"


def clean_mathml(node: Tag) -> str:
    """Strip LaTeXML's presentation/content pairing down to plain MathML.

    LaTeXML wraps its markup in ``<semantics>`` alongside an ``<annotation>``
    holding the original TeX. ziamath does not know those elements, so we unwrap
    the presentation half and drop the rest.
    """
    math = BeautifulSoup(str(node), "xml").find("math")
    if math is None:
        raise MathRenderError("element is not MathML")

    for annotation in math.find_all(["annotation", "annotation-xml"]):
        annotation.decompose()
    for semantics in math.find_all("semantics"):
        semantics.unwrap()
    for element in [math, *math.find_all(True)]:
        for attribute in list(element.attrs):
            if attribute in _DROPPED_MATH_ATTRS or attribute.startswith("data-"):
                del element[attribute]
    return str(math)


def _geometry(svg: str) -> tuple[float, float, float]:
    """Read width, height and below-baseline depth (in em) out of an SVG.

    ziamath draws with the maths baseline at ``y = 0``, so the viewBox tells us
    directly how far the glyphs descend below it.
    """
    root = ElementTree.fromstring(svg)
    view_box = root.get("viewBox")
    if not view_box:
        raise MathRenderError("rendered SVG has no viewBox")
    min_x, min_y, box_width, box_height = (
        float(value) for value in _VIEWBOX.split(view_box.strip())
    )
    del min_x
    depth = min_y + box_height - _RENDER_MARGIN
    return (
        box_width / _RENDER_SIZE,
        box_height / _RENDER_SIZE,
        depth / _RENDER_SIZE,
    )


def _draw(mathml: str, latex: str, color: str | None) -> str:
    """Draw MathML, falling back to the TeX annotation when that fails."""
    try:
        drawing = ziamath.Math(mathml, size=_RENDER_SIZE, margin=_RENDER_MARGIN)
        return drawing.svg()
    except Exception as mathml_error:  # noqa: BLE001 - backend raises many types
        if not latex:
            raise MathRenderError(f"could not draw MathML: {mathml_error}") from None
        log.debug("MathML draw failed (%s); retrying from TeX", mathml_error)
        try:
            drawing = ziamath.Latex(
                latex, size=_RENDER_SIZE, color=color, margin=_RENDER_MARGIN
            )
            return drawing.svg()
        except Exception as latex_error:  # noqa: BLE001
            raise MathRenderError(
                f"could not draw {latex!r}: {latex_error}"
            ) from None


def _recolour(svg: str, color: str) -> str:
    """Force every drawn path to one colour.

    ziamath paints with the default fill, which is fine on paper-white screens
    but disappears in dark mode; ``currentColor`` lets the reader's own text
    colour drive the equation instead.
    """
    if "fill=" in svg:
        svg = re.sub(r'fill="(?!none)[^"]*"', f'fill="{color}"', svg)
    return svg.replace("<svg ", f'<svg fill="{color}" ', 1)


class MathRenderer:
    """Draws equations, reusing the image whenever the same maths repeats."""

    def __init__(self, output_format: str = "svg", png_scale: float = 3.0):
        if output_format not in MATH_FORMATS:
            raise ValueError(
                f"unknown math format {output_format!r}; expected one of {MATH_FORMATS}"
            )
        self.output_format = output_format
        self.png_scale = png_scale
        self.cache: dict[str, RenderedMath] = {}
        self.failures: list[tuple[str, str]] = []

    @property
    def inline_svg(self) -> bool:
        return self.output_format == "inline-svg"

    def render(self, node: Tag) -> RenderedMath:
        """Draw one ``<math>`` element."""
        latex = (node.get("alttext") or "").strip()
        display = (node.get("display") or "inline") == "block"
        mathml = clean_mathml(node)

        key_source = f"{self.output_format}|{display}|{mathml}"
        key = hashlib.sha256(key_source.encode("utf-8")).hexdigest()[:20]
        if cached := self.cache.get(key):
            return cached

        # Inline SVG can inherit the reader's text colour; an external image
        # is an isolated document, so it has to carry a literal colour.
        colour = "currentColor" if self.inline_svg else "#000000"
        svg = _recolour(_draw(mathml, latex, colour), colour)
        width_em, height_em, depth_em = _geometry(svg)

        if self.output_format == "png":
            import cairosvg  # imported lazily; only this format needs Cairo

            data = cairosvg.svg2png(
                bytestring=svg.encode("utf-8"), scale=self.png_scale
            )
            media_type = "image/png"
        else:
            data = svg.encode("utf-8")
            media_type = "image/svg+xml"

        rendered = RenderedMath(
            key=key,
            data=data,
            media_type=media_type,
            width_em=round(width_em, 3),
            height_em=round(height_em, 3),
            depth_em=round(depth_em, 3),
            latex=latex,
            display=display,
            svg_markup=svg if self.inline_svg else "",
        )
        self.cache[key] = rendered
        return rendered

    def record_failure(self, latex: str, reason: str) -> None:
        self.failures.append((latex, reason))
