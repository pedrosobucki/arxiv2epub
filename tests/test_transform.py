from __future__ import annotations

from bs4 import BeautifulSoup

from arxiv2epub.mathrender import MathRenderer
from arxiv2epub.transform import transform
from conftest import make_source

EQUATION_TABLE = """
<section class="ltx_section" id="S1"><h2 class="ltx_title">1 Introduction</h2>
<table class="ltx_equation ltx_eqn_table" id="S1.E1"><tbody><tr>
  <td class="ltx_eqn_cell"><math alttext="x=1" display="block"><mi>x</mi></math></td>
  <td class="ltx_eqn_cell ltx_eqn_eqno"><span class="ltx_tag">(1)</span></td>
</tr></tbody></table>
</section>
"""


def _build(body: str, fetcher, metadata, **kwargs):
    return transform(make_source(body), metadata, fetcher, MathRenderer(), **kwargs)


def _soup(chapter):
    return BeautifulSoup(chapter.body, "html.parser")


def test_the_paper_is_split_into_one_file_per_section(fetcher, paper_metadata) -> None:
    body = (
        '<section class="ltx_section" id="S1"><h2>1 Introduction</h2><p>a</p></section>'
        '<section class="ltx_section" id="S2"><h2>2 Method</h2><p>b</p></section>'
        '<section class="ltx_bibliography" id="bib"><h2>References</h2></section>'
    )
    book = _build(body, fetcher, paper_metadata)
    assert [c.title for c in book.chapters] == [
        "Title & Abstract",
        "1 Introduction",
        "2 Method",
        "References",
    ]
    assert book.chapters[-1].epub_type == "bibliography"


def test_section_titles_become_the_files_top_heading(fetcher, paper_metadata) -> None:
    body = (
        '<section class="ltx_section" id="S1"><h2>1 Introduction</h2>'
        '<section class="ltx_subsection" id="S1.SS1"><h3>1.1 Setup</h3></section>'
        "</section>"
    )
    book = _build(body, fetcher, paper_metadata)
    soup = _soup(book.chapters[1])
    assert soup.find("h1").get_text(strip=True) == "1 Introduction"
    assert soup.find("h2").get_text(strip=True) == "1.1 Setup"


def test_subsections_are_listed_under_their_section(fetcher, paper_metadata) -> None:
    body = (
        '<section class="ltx_section" id="S1"><h2>1 Introduction</h2>'
        '<section class="ltx_subsection" id="S1.SS1"><h3>1.1 Setup</h3></section>'
        "</section>"
    )
    book = _build(body, fetcher, paper_metadata)
    assert book.chapters[1].toc[0].title == "1.1 Setup"
    assert book.chapters[1].toc[0].href == "ch01.xhtml#S1.SS1"


def test_equation_tables_become_blocks_with_a_floated_number(fetcher, paper_metadata) -> None:
    book = _build(EQUATION_TABLE, fetcher, paper_metadata)
    soup = _soup(book.chapters[1])
    assert soup.find("table") is None
    equation = soup.find("div", class_="equation")
    assert equation.find("span", class_="equation-number").get_text() == "(1)"
    assert equation.find("img", class_="math-display") is not None


def test_inline_maths_is_sized_and_aligned_in_em(fetcher, paper_metadata) -> None:
    body = (
        '<section class="ltx_section" id="S1"><h2>1</h2>'
        '<p>a <math alttext="d_{k}" display="inline"><msub><mi>d</mi><mi>k</mi></msub>'
        "</math> b</p></section>"
    )
    book = _build(body, fetcher, paper_metadata)
    style = _soup(book.chapters[1]).find("img", class_="math-inline")["style"]
    assert "em" in style and "vertical-align:-" in style


def test_footnotes_become_epub_popups(fetcher, paper_metadata) -> None:
    body = (
        '<section class="ltx_section" id="S1"><h2>1</h2><p>text'
        '<span class="ltx_note ltx_role_footnote"><sup class="ltx_note_mark">1</sup>'
        '<span class="ltx_note_content"><span class="ltx_note_type">footnote: </span>'
        "The note.</span></span></p></section>"
    )
    book = _build(body, fetcher, paper_metadata)
    soup = _soup(book.chapters[1])
    reference = soup.find("a", class_="noteref")
    assert reference["epub:type"] == "noteref"
    note = soup.find("aside", class_="footnote")
    assert note["id"] == reference["href"].lstrip("#")
    assert "The note." in note.get_text()
    assert "footnote:" not in note.get_text()


def test_a_reference_into_another_section_gets_that_sections_filename(
    fetcher, paper_metadata
) -> None:
    body = (
        '<section class="ltx_section" id="S1"><h2>1</h2>'
        '<p>see <a href="#S2">two</a></p></section>'
        '<section class="ltx_section" id="S2"><h2>2</h2></section>'
    )
    book = _build(body, fetcher, paper_metadata)
    assert _soup(book.chapters[1]).find("a")["href"] == "ch02.xhtml#S2"


def test_a_link_to_a_dropped_anchor_becomes_plain_text(fetcher, paper_metadata) -> None:
    body = (
        '<section class="ltx_section" id="S1"><h2>1</h2>'
        '<p>see <a href="#gone">this</a></p></section>'
    )
    book = _build(body, fetcher, paper_metadata)
    soup = _soup(book.chapters[1])
    assert soup.find("a") is None
    assert "see this" in soup.get_text()


def test_site_chrome_and_scripts_do_not_reach_the_book(fetcher, paper_metadata) -> None:
    body = (
        '<div class="ltx_page_navbar">nav</div><script>alert(1)</script>'
        '<div class="ltx_TOC">contents</div>'
        '<section class="ltx_section" id="S1"><h2>1</h2><p>real text</p></section>'
    )
    book = _build(body, fetcher, paper_metadata)
    combined = " ".join(c.body for c in book.chapters)
    assert "real text" in combined
    for unwanted in ("alert(1)", "ltx_page_navbar", "ltx_TOC"):
        assert unwanted not in combined


def test_a_caption_between_panels_is_demoted_so_the_figure_stays_valid(
    fetcher, paper_metadata
) -> None:
    body = (
        '<section class="ltx_section" id="S1"><h2>1</h2>'
        '<figure id="F1"><div>panel a</div><figcaption>(a)</figcaption>'
        "<div>panel b</div><figcaption>Figure 1</figcaption></figure></section>"
    )
    book = _build(body, fetcher, paper_metadata)
    figure = _soup(book.chapters[1]).find("figure")
    captions = figure.find_all("figcaption", recursive=False)
    assert len(captions) == 1
    assert captions[0].get_text() == "Figure 1"
    assert "(a)" in figure.get_text()


def test_object_graphics_are_fetched_as_images(paper_metadata) -> None:
    from conftest import StubFetcher

    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="2" height="2"/></svg>'
    fetcher = StubFetcher({"https://arxiv.org/html/plot.svg": svg})
    body = (
        '<section class="ltx_section" id="S1"><h2>1</h2>'
        '<figure><object type="image/svg+xml" data="plot.svg"></object></figure>'
        "</section>"
    )
    book = _build(body, fetcher, paper_metadata)
    soup = _soup(book.chapters[1])
    assert soup.find("object") is None
    assert soup.find("img")["src"].endswith(".svg")
    assert any(asset.media_type == "image/svg+xml" for asset in book.assets)


def test_a_missing_figure_is_reported_rather_than_left_broken(fetcher, paper_metadata) -> None:
    body = (
        '<section class="ltx_section" id="S1"><h2>1</h2>'
        '<figure><img src="missing.png" alt="x"/></figure></section>'
    )
    book = _build(body, fetcher, paper_metadata)
    assert _soup(book.chapters[1]).find("img") is None
    assert any("missing.png" in warning for warning in book.warnings)


def test_inline_svg_keeps_its_namespace(fetcher, paper_metadata) -> None:
    body = (
        '<section class="ltx_section" id="S1"><h2>1</h2>'
        '<svg class="ltx_picture" viewBox="0 0 4 4"><rect width="2" height="2"/></svg>'
        "</section>"
    )
    book = _build(body, fetcher, paper_metadata)
    assert 'xmlns="http://www.w3.org/2000/svg"' in book.chapters[1].body
    assert "viewBox" in book.chapters[1].body


def test_the_title_page_carries_the_metadata(fetcher, paper_metadata) -> None:
    book = _build(EQUATION_TABLE, fetcher, paper_metadata)
    titlepage = book.chapters[0].body
    assert paper_metadata.title in titlepage
    assert "Ashish Vaswani" in titlepage
    assert "arXiv:1706.03762v7" in titlepage
