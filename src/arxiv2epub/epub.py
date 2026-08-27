"""Writing the EPUB container.

Targets EPUB 3 but keeps the EPUB 2 fallbacks — an NCX table of contents, a
``<guide>``, and a ``<meta name="cover">`` — because Amazon's Send-to-Kindle
converter still reads them and produces a better result when they are present.
"""

from __future__ import annotations

import logging
import zipfile
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from lxml import etree

from . import cover as cover_module
from .models import Asset, Book, TocEntry
from .xhtml import page

log = logging.getLogger(__name__)

TEXT_DIR = "text"
CONTAINER_XML = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EpubWriter:
    """Assembles a :class:`Book` into an .epub file on disk."""

    def __init__(self, book: Book, *, include_cover: bool = True):
        self.book = book
        self.include_cover = include_cover
        self.metadata = book.metadata
        self.uid = f"urn:arxiv:{self.metadata.arxiv_id.versioned}"
        self.cover_asset: Asset | None = None

    # ------------------------------------------------------------------ write

    def write(self, destination: Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)

        files = self._collect_files()
        with zipfile.ZipFile(destination, "w") as archive:
            # The spec requires an uncompressed "mimetype" entry written first.
            archive.writestr(
                zipfile.ZipInfo("mimetype"),
                "application/epub+zip",
                compress_type=zipfile.ZIP_STORED,
            )
            for path, data in files:
                archive.writestr(path, data, compress_type=zipfile.ZIP_DEFLATED)
        return destination

    def _collect_files(self) -> list[tuple[str, bytes]]:
        files: list[tuple[str, bytes]] = [
            ("META-INF/container.xml", CONTAINER_XML.encode("utf-8")),
            ("OEBPS/style.css", self._stylesheet()),
        ]

        if self.include_cover:
            image = cover_module.render(self.metadata)
            if image:
                self.cover_asset = Asset(
                    path="images/cover.jpg",
                    data=image,
                    media_type="image/jpeg",
                    properties="cover-image",
                )
                files.append((f"OEBPS/{self.cover_asset.path}", image))
                files.append(
                    (f"OEBPS/{TEXT_DIR}/cover.xhtml", self._cover_page().encode("utf-8"))
                )

        for chapter in self.book.chapters:
            document = page(
                title=f"{chapter.title} — {self.metadata.title}",
                body=chapter.body,
                stylesheet="../style.css",
                epub_type=chapter.epub_type,
            )
            chapter.properties = self._inspect(chapter.filename, document)
            files.append((f"OEBPS/{TEXT_DIR}/{chapter.filename}", document.encode("utf-8")))

        for asset in self.book.assets:
            files.append((f"OEBPS/{asset.path}", asset.data))

        files.append(("OEBPS/nav.xhtml", self._nav().encode("utf-8")))
        files.append(("OEBPS/toc.ncx", self._ncx().encode("utf-8")))
        files.append(("OEBPS/content.opf", self._opf().encode("utf-8")))
        return files

    def _inspect(self, filename: str, document: str) -> str:
        """Check a chapter parses, and report the manifest properties it needs.

        An EPUB reader parses content as XML and simply refuses a file that is
        not well formed, usually with no useful message, so it is worth paying
        the parse to find out first. The same pass spots inline SVG and MathML,
        which the spec requires the manifest to declare.
        """
        parser = etree.XMLParser(resolve_entities=False, load_dtd=False, recover=False)
        try:
            root = etree.fromstring(document.encode("utf-8"), parser)
        except etree.XMLSyntaxError as exc:
            message = f"{filename} is not well-formed XHTML: {exc}"
            log.error(message)
            self.book.warnings.append(message)
            return ""

        seen: set[str] = set()
        namespaces: set[str] = set()
        for element in root.iter():
            if isinstance(element.tag, str) and element.tag.startswith("{"):
                namespaces.add(element.tag[1:].split("}", 1)[0])
            identifier = element.get("id")
            if not identifier:
                continue
            if identifier in seen:
                message = f"{filename} repeats the id {identifier!r}"
                log.warning(message)
                self.book.warnings.append(message)
            seen.add(identifier)

        properties = []
        if "http://www.w3.org/2000/svg" in namespaces:
            properties.append("svg")
        if "http://www.w3.org/1998/Math/MathML" in namespaces:
            properties.append("mathml")
        return " ".join(properties)

    @staticmethod
    def _stylesheet() -> bytes:
        return (
            resources.files("arxiv2epub.resources").joinpath("style.css").read_bytes()
        )

    # ------------------------------------------------------------------ pages

    def _cover_page(self) -> str:
        body = (
            '<div class="cover-page" style="text-align:center;margin:0;padding:0;">'
            f'<img src="../{self.cover_asset.path}" alt="Cover" '
            'style="max-width:100%;max-height:100%;"/>'
            "</div>"
        )
        return page(
            title="Cover", body=body, stylesheet="../style.css", epub_type="cover"
        )

    def _toc_entries(self) -> list[TocEntry]:
        entries: list[TocEntry] = []
        for chapter in self.book.chapters:
            if not chapter.in_toc:
                continue
            entries.append(
                TocEntry(
                    title=chapter.title,
                    href=f"{TEXT_DIR}/{chapter.filename}",
                    children=[
                        TocEntry(child.title, f"{TEXT_DIR}/{child.href}")
                        for child in chapter.toc
                    ],
                )
            )
        return entries

    def _nav(self) -> str:
        def render(entries: list[TocEntry], depth: int) -> str:
            pad = "  " * depth
            lines = [f"{pad}<ol>"]
            for entry in entries:
                lines.append(
                    f'{pad}  <li><a href={quoteattr(entry.href)}>'
                    f"{escape(entry.title)}</a>"
                )
                if entry.children:
                    lines.append(render(entry.children, depth + 2))
                lines.append(f"{pad}  </li>")
            lines.append(f"{pad}</ol>")
            return "\n".join(lines)

        body = (
            '<nav epub:type="toc" id="toc" role="doc-toc">\n'
            "<h1>Contents</h1>\n"
            f"{render(self._toc_entries(), 0)}\n"
            "</nav>\n"
            '<nav epub:type="landmarks" id="landmarks" hidden="hidden">\n'
            "<ol>\n"
            f'<li><a epub:type="bodymatter" href="{TEXT_DIR}/'
            f'{self.book.chapters[0].filename}">Start of content</a></li>\n'
            "</ol>\n</nav>"
        )
        return page(title="Contents", body=body, stylesheet="style.css")

    def _ncx(self) -> str:
        counter = iter(range(1, 10_000))

        def render(entries: list[TocEntry], depth: int) -> str:
            lines: list[str] = []
            for entry in entries:
                order = next(counter)
                pad = "  " * (depth + 2)
                lines.append(
                    f'{pad}<navPoint id="nav{order}" playOrder="{order}">\n'
                    f"{pad}  <navLabel><text>{escape(entry.title)}</text></navLabel>\n"
                    f"{pad}  <content src={quoteattr(entry.href)}/>"
                )
                if entry.children:
                    lines.append(render(entry.children, depth + 1))
                lines.append(f"{pad}</navPoint>")
            return "\n".join(lines)

        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
            "  <head>\n"
            f'    <meta name="dtb:uid" content={quoteattr(self.uid)}/>\n'
            '    <meta name="dtb:depth" content="2"/>\n'
            '    <meta name="dtb:totalPageCount" content="0"/>\n'
            '    <meta name="dtb:maxPageNumber" content="0"/>\n'
            "  </head>\n"
            f"  <docTitle><text>{escape(self.metadata.title)}</text></docTitle>\n"
            f"  <docAuthor><text>{escape(self.metadata.author_line)}</text></docAuthor>\n"
            "  <navMap>\n"
            f"{render(self._toc_entries(), 0)}\n"
            "  </navMap>\n"
            "</ncx>\n"
        )

    # -------------------------------------------------------------- package

    def _manifest(self) -> list[str]:
        items = [
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
            'properties="nav"/>',
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
            '<item id="style" href="style.css" media-type="text/css"/>',
        ]
        if self.cover_asset:
            items.append(
                f'<item id="cover-image" href={quoteattr(self.cover_asset.path)} '
                'media-type="image/jpeg" properties="cover-image"/>'
            )
            items.append(
                f'<item id="cover" href="{TEXT_DIR}/cover.xhtml" '
                'media-type="application/xhtml+xml"/>'
            )
        for chapter in self.book.chapters:
            properties = (
                f" properties={quoteattr(chapter.properties)}"
                if chapter.properties
                else ""
            )
            items.append(
                f'<item id={quoteattr(chapter.identifier)} '
                f'href="{TEXT_DIR}/{chapter.filename}" '
                f'media-type="application/xhtml+xml"{properties}/>'
            )
        for asset in self.book.assets:
            items.append(
                f'<item id={quoteattr(asset.manifest_id)} '
                f'href={quoteattr(asset.path)} '
                f'media-type={quoteattr(asset.media_type)}/>'
            )
        return items

    def _spine(self) -> list[str]:
        entries = []
        if self.cover_asset:
            entries.append('<itemref idref="cover" linear="yes"/>')
        entries.extend(
            f'<itemref idref={quoteattr(chapter.identifier)}/>'
            for chapter in self.book.chapters
        )
        return entries

    def _opf(self) -> str:
        meta = self.metadata
        creators: list[str] = []
        for index, author in enumerate(meta.authors, start=1):
            creators.append(
                f'    <dc:creator id="creator{index}">{escape(author)}</dc:creator>\n'
                f'    <meta refines="#creator{index}" property="role" '
                'scheme="marc:relators">aut</meta>\n'
                f'    <meta refines="#creator{index}" property="display-seq">'
                f"{index}</meta>"
            )
        if not creators:
            creators.append("    <dc:creator>Unknown author</dc:creator>")

        subjects = "\n".join(
            f"    <dc:subject>{escape(category)}</dc:subject>"
            for category in meta.categories
        )
        date_line = (
            f"    <dc:date>{meta.published.isoformat()}</dc:date>"
            if meta.published
            else ""
        )
        source_line = f"    <dc:source>{escape(meta.arxiv_id.abs_url)}</dc:source>"
        identifiers = [f'    <dc:identifier id="pub-id">{escape(self.uid)}</dc:identifier>']
        if meta.doi:
            identifiers.append(
                f"    <dc:identifier>urn:doi:{escape(meta.doi)}</dc:identifier>"
            )
        cover_meta = (
            '    <meta name="cover" content="cover-image"/>' if self.cover_asset else ""
        )
        guide = (
            '  <guide>\n'
            f'    <reference type="cover" title="Cover" href="{TEXT_DIR}/cover.xhtml"/>\n'
            "  </guide>"
            if self.cover_asset
            else ""
        )

        blocks = [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
            'unique-identifier="pub-id" xml:lang="en">',
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:opf="http://www.idpf.org/2007/opf">',
            *identifiers,
            f"    <dc:title>{escape(meta.title)}</dc:title>",
            *creators,
            "    <dc:language>en</dc:language>",
            f"    <dc:description>{escape(meta.abstract)}</dc:description>",
            f'    <dc:publisher>arXiv</dc:publisher>',
            subjects,
            date_line,
            source_line,
            f'    <meta property="dcterms:modified">{_now()}</meta>',
            '    <meta property="rendition:layout">reflowable</meta>',
            cover_meta,
            "  </metadata>",
            "  <manifest>",
            *(f"    {item}" for item in self._manifest()),
            "  </manifest>",
            '  <spine toc="ncx">',
            *(f"    {item}" for item in self._spine()),
            "  </spine>",
            guide,
            "</package>",
        ]
        return "\n".join(line for line in blocks if line.strip()) + "\n"


def write(book: Book, destination: Path, *, include_cover: bool = True) -> Path:
    return EpubWriter(book, include_cover=include_cover).write(destination)


__all__ = ["EpubWriter", "write"]
