"""Serialising a parsed tree back out as EPUB-safe XHTML.

Two things need care. HTML parsers lower-case every attribute name, which
silently breaks SVG's camel-cased attributes such as ``viewBox``, so those are
restored on the way out. And EPUB readers want real XHTML, so void elements are
closed and the ``epub`` namespace is declared on the root element.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

# The case-restoration table from the HTML5 foreign-content rules; without it a
# parsed-then-reserialised <svg> loses its viewBox and stops scaling.
SVG_ATTRIBUTE_CASE = {
    "attributename": "attributeName",
    "attributetype": "attributeType",
    "basefrequency": "baseFrequency",
    "baseprofile": "baseProfile",
    "calcmode": "calcMode",
    "clippathunits": "clipPathUnits",
    "diffuseconstant": "diffuseConstant",
    "edgemode": "edgeMode",
    "filterunits": "filterUnits",
    "glyphref": "glyphRef",
    "gradienttransform": "gradientTransform",
    "gradientunits": "gradientUnits",
    "kernelmatrix": "kernelMatrix",
    "kernelunitlength": "kernelUnitLength",
    "keypoints": "keyPoints",
    "keysplines": "keySplines",
    "keytimes": "keyTimes",
    "lengthadjust": "lengthAdjust",
    "limitingconeangle": "limitingConeAngle",
    "markerheight": "markerHeight",
    "markerunits": "markerUnits",
    "markerwidth": "markerWidth",
    "maskcontentunits": "maskContentUnits",
    "maskunits": "maskUnits",
    "numoctaves": "numOctaves",
    "pathlength": "pathLength",
    "patterncontentunits": "patternContentUnits",
    "patterntransform": "patternTransform",
    "patternunits": "patternUnits",
    "pointsatx": "pointsAtX",
    "pointsaty": "pointsAtY",
    "pointsatz": "pointsAtZ",
    "preservealpha": "preserveAlpha",
    "preserveaspectratio": "preserveAspectRatio",
    "primitiveunits": "primitiveUnits",
    "refx": "refX",
    "refy": "refY",
    "repeatcount": "repeatCount",
    "repeatdur": "repeatDur",
    "requiredextensions": "requiredExtensions",
    "requiredfeatures": "requiredFeatures",
    "specularconstant": "specularConstant",
    "specularexponent": "specularExponent",
    "spreadmethod": "spreadMethod",
    "startoffset": "startOffset",
    "stddeviation": "stdDeviation",
    "stitchtiles": "stitchTiles",
    "surfacescale": "surfaceScale",
    "systemlanguage": "systemLanguage",
    "tablevalues": "tableValues",
    "targetx": "targetX",
    "targety": "targetY",
    "textlength": "textLength",
    "viewbox": "viewBox",
    "viewtarget": "viewTarget",
    "xchannelselector": "xChannelSelector",
    "ychannelselector": "yChannelSelector",
    "zoomandpan": "zoomAndPan",
}

# SVG element names an HTML parser also lower-cases. Unlike the attributes,
# none of these collide with an HTML tag, so restoring them is unambiguous.
SVG_TAG_CASE = {
    "altglyph": "altGlyph",
    "altglyphdef": "altGlyphDef",
    "altglyphitem": "altGlyphItem",
    "animatecolor": "animateColor",
    "animatemotion": "animateMotion",
    "animatetransform": "animateTransform",
    "clippath": "clipPath",
    "feblend": "feBlend",
    "fecolormatrix": "feColorMatrix",
    "fecomponenttransfer": "feComponentTransfer",
    "fecomposite": "feComposite",
    "feconvolvematrix": "feConvolveMatrix",
    "fediffuselighting": "feDiffuseLighting",
    "fedisplacementmap": "feDisplacementMap",
    "fedistantlight": "feDistantLight",
    "feflood": "feFlood",
    "fefunca": "feFuncA",
    "fefuncb": "feFuncB",
    "fefuncg": "feFuncG",
    "fefuncr": "feFuncR",
    "fegaussianblur": "feGaussianBlur",
    "feimage": "feImage",
    "femerge": "feMerge",
    "femergenode": "feMergeNode",
    "femorphology": "feMorphology",
    "feoffset": "feOffset",
    "fepointlight": "fePointLight",
    "fespecularlighting": "feSpecularLighting",
    "fespotlight": "feSpotLight",
    "fetile": "feTile",
    "feturbulence": "feTurbulence",
    "foreignobject": "foreignObject",
    "glyphref": "glyphRef",
    "lineargradient": "linearGradient",
    "radialgradient": "radialGradient",
    "textpath": "textPath",
}

_ATTRIBUTE = re.compile(r'(?<=[\s"])([a-z]+)=', re.ASCII)
_TAG = re.compile(r"<(/?)(" + "|".join(SVG_TAG_CASE) + r")(?=[\s/>])", re.ASCII)

XHTML_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" \
xml:lang="{lang}" lang="{lang}">
<head>
<meta charset="utf-8"/>
<title>{title}</title>
<link rel="stylesheet" type="text/css" href="{stylesheet}"/>
</head>
<body{body_attributes}>
{body}
</body>
</html>
"""


def restore_svg_case(markup: str) -> str:
    """Put the camelCase back into SVG element and attribute names.

    An HTML parser lower-cases both, which turns ``viewBox`` into an unknown
    attribute and ``foreignObject`` into an unknown element.
    """

    def attribute(match: re.Match[str]) -> str:
        name = match.group(1)
        return f"{SVG_ATTRIBUTE_CASE.get(name, name)}="

    def tag(match: re.Match[str]) -> str:
        return f"<{match.group(1)}{SVG_TAG_CASE[match.group(2)]}"

    return _TAG.sub(tag, _ATTRIBUTE.sub(attribute, markup))


def escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def serialize(tag: Tag) -> str:
    """Render a tag, and everything inside it, as XHTML."""
    return restore_svg_case(tag.decode(formatter="minimal"))


def serialize_children(tag: Tag) -> str:
    """Render a tag's contents as XHTML."""
    return restore_svg_case(tag.decode_contents(formatter="minimal"))


def parse_fragment(markup: str) -> Tag:
    """Parse a self-contained markup string (an SVG, say) into one tag."""
    soup = BeautifulSoup(markup, "xml")
    root = next((child for child in soup.children if isinstance(child, Tag)), None)
    if root is None:
        raise ValueError("fragment contained no element")
    return root


def page(
    *,
    title: str,
    body: str,
    stylesheet: str = "style.css",
    lang: str = "en",
    epub_type: str = "",
) -> str:
    """Wrap a body fragment in a complete XHTML document."""
    body_attributes = f' epub:type="{epub_type}"' if epub_type else ""
    return XHTML_TEMPLATE.format(
        lang=lang,
        title=escape(title),
        stylesheet=stylesheet,
        body=body,
        body_attributes=body_attributes,
    )
