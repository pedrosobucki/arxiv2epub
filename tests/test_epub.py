from __future__ import annotations

import zipfile

from lxml import etree

from arxiv2epub.epub import EpubWriter
from arxiv2epub.models import Asset, Book, Chapter, TocEntry
from conftest import make_source

OPF_NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}


def _book(paper_metadata) -> Book:
    book = Book(metadata=paper_metadata, source=make_source("<p>x</p>"))
    book.chapters = [
        Chapter("titlepage", "titlepage.xhtml", "Title & Abstract", "<p>abstract</p>"),
        Chapter(
            "ch01",
            "ch01.xhtml",
            "1 Introduction",
            '<section id="S1"><h1>1 Introduction</h1><p>text</p></section>',
            toc=[TocEntry("1.1 Setup", "ch01.xhtml#S1.SS1")],
        ),
    ]
    book.assets = [
        Asset("math/m1.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>', "image/svg+xml")
    ]
    return book


def _write(tmp_path, book, **kwargs) -> zipfile.ZipFile:
    path = EpubWriter(book, **kwargs).write(tmp_path / "book.epub")
    return zipfile.ZipFile(path)


def test_the_mimetype_entry_comes_first_and_uncompressed(tmp_path, paper_metadata) -> None:
    archive = _write(tmp_path, _book(paper_metadata), include_cover=False)
    first = archive.infolist()[0]
    assert first.filename == "mimetype"
    assert first.compress_type == zipfile.ZIP_STORED
    assert archive.read("mimetype") == b"application/epub+zip"


def test_every_manifest_entry_actually_exists(tmp_path, paper_metadata) -> None:
    archive = _write(tmp_path, _book(paper_metadata))
    opf = etree.fromstring(archive.read("OEBPS/content.opf"))
    hrefs = [
        item.get("href")
        for item in opf.findall(".//opf:manifest/opf:item", OPF_NS)
    ]
    names = set(archive.namelist())
    assert hrefs
    for href in hrefs:
        assert f"OEBPS/{href}" in names


def test_the_package_records_each_author_in_order(tmp_path, paper_metadata) -> None:
    archive = _write(tmp_path, _book(paper_metadata))
    opf = etree.fromstring(archive.read("OEBPS/content.opf"))
    creators = [node.text for node in opf.findall(".//dc:creator", OPF_NS)]
    assert creators == paper_metadata.authors


def test_both_navigation_documents_are_written(tmp_path, paper_metadata) -> None:
    archive = _write(tmp_path, _book(paper_metadata))
    # EPUB 3 readers use nav.xhtml; Send-to-Kindle still reads the NCX.
    nav = archive.read("OEBPS/nav.xhtml").decode()
    ncx = etree.fromstring(archive.read("OEBPS/toc.ncx"))
    assert "1 Introduction" in nav
    assert "1.1 Setup" in nav
    labels = [node.text for node in ncx.iter("{*}text")]
    assert "1 Introduction" in labels


def test_a_chapter_holding_inline_svg_declares_it(tmp_path, paper_metadata) -> None:
    book = _book(paper_metadata)
    book.chapters[1].body = (
        '<section id="S1"><svg xmlns="http://www.w3.org/2000/svg"/></section>'
    )
    archive = _write(tmp_path, book)
    opf = archive.read("OEBPS/content.opf").decode()
    assert 'properties="svg"' in opf


def test_malformed_markup_is_reported_not_shipped_silently(tmp_path, paper_metadata) -> None:
    book = _book(paper_metadata)
    book.chapters[1].body = "<p>unclosed"
    _write(tmp_path, book)
    assert any("well-formed" in warning for warning in book.warnings)


def test_a_repeated_id_is_reported(tmp_path, paper_metadata) -> None:
    book = _book(paper_metadata)
    book.chapters[1].body = '<div id="dup"></div><div id="dup"></div>'
    _write(tmp_path, book)
    assert any("repeats the id" in warning for warning in book.warnings)


def test_a_cover_is_generated_and_referenced(tmp_path, paper_metadata) -> None:
    archive = _write(tmp_path, _book(paper_metadata))
    assert "OEBPS/images/cover.jpg" in archive.namelist()
    opf = archive.read("OEBPS/content.opf").decode()
    assert 'properties="cover-image"' in opf
    assert '<meta name="cover" content="cover-image"/>' in opf


def test_the_cover_can_be_left_out(tmp_path, paper_metadata) -> None:
    archive = _write(tmp_path, _book(paper_metadata), include_cover=False)
    assert not [n for n in archive.namelist() if "cover" in n]
