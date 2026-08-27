from __future__ import annotations

from lxml import etree

from arxiv2epub.xhtml import page, restore_svg_case


def test_svg_attribute_case_survives_a_parse() -> None:
    markup = '<svg viewbox="0 0 10 10" preserveaspectratio="none" width="10"/>'
    restored = restore_svg_case(markup)
    assert 'viewBox="0 0 10 10"' in restored
    assert 'preserveAspectRatio="none"' in restored
    assert 'width="10"' in restored


def test_ordinary_attributes_are_left_alone() -> None:
    markup = '<img src="a.png" alt="x" class="y"/>'
    assert restore_svg_case(markup) == markup


def test_svg_element_names_survive_a_parse() -> None:
    markup = "<svg><foreignobject><clippath/></foreignobject></svg>"
    restored = restore_svg_case(markup)
    assert "<foreignObject>" in restored
    assert "</foreignObject>" in restored
    assert "<clipPath/>" in restored


def test_html_elements_are_not_renamed() -> None:
    markup = "<div><p>text</p><object data='x'/></div>"
    assert restore_svg_case(markup) == markup


def test_a_page_is_well_formed_xml() -> None:
    document = page(title="A & B <c>", body="<p>hello</p>", epub_type="chapter")
    root = etree.fromstring(document.encode("utf-8"))
    assert root.tag == "{http://www.w3.org/1999/xhtml}html"
    assert "A &amp; B &lt;c&gt;" in document
