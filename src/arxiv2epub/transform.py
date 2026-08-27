"""Turning LaTeXML's HTML into clean, chapterised XHTML.

arXiv's HTML is already semantic: sections are ``<section>``, equations are
MathML, figures carry real ``<figcaption>``s. So this is a rewrite in place
rather than a re-extraction. The work is stripping site chrome, replacing maths
with images, flattening LaTeXML's table-based equation layout, turning footnotes
into EPUB popups, pulling figures local, and splitting the result into one file
per top-level section.

Chapters are carried as live element trees until the very end. Serialising early
and re-parsing to fix up links would let an HTML parser quietly restructure
nested figures, which is exactly the markup arXiv papers are full of.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from .http import Fetcher
from .images import prepare as prepare_image
from .mathrender import MathRenderError, MathRenderer, RenderedMath
from .metadata import PaperMetadata
from .models import Asset, Book, Chapter, TocEntry
from .sources import HtmlSource
from .xhtml import parse_fragment, serialize

log = logging.getLogger(__name__)

SVG_NAMESPACE = "http://www.w3.org/2000/svg"

# Page furniture from arxiv.org and ar5iv that is not part of the paper.
CHROME_SELECTORS = (
    "script, style, noscript, template, iframe, form, button, input, select",
    "nav, header.ltx_page_header, footer.ltx_page_footer",
    ".ltx_page_navbar, .ltx_page_logo, .ltx_TOC, .ltx_pagination",
    "#latexml-warning, .package-alerts, .ar5iv-footer, .ltx_engrafo_container",
    ".extra-services, .desktop_header, .mobile_header, .abs-button",
    # arXiv attaches a base64 "download this listing" link to every code block.
    ".ltx_listing_data",
)

# Tags with no meaning in a book; their text is kept, the wrapper is not.
UNWRAP_TAGS = {"font", "center", "big", "tt", "u", "s", "strike", "acronym", "wbr"}

DROP_ATTRIBUTES = {
    "style",
    "title",
    "srcset",
    "sizes",
    "loading",
    "decoding",
    "tabindex",
    "target",
    "rel",
    "role",
    "align",
    "valign",
    "border",
    "cellpadding",
    "cellspacing",
    "bgcolor",
}
DROP_ATTRIBUTE_PREFIXES = ("data-", "on", "aria-", "xml:")

# Sections that become their own file, in the order LaTeXML emits them.
TOP_LEVEL_SECTION_CLASSES = (
    "ltx_section",
    "ltx_appendix",
    "ltx_bibliography",
    "ltx_acknowledgements",
    "ltx_index",
)

IMAGE_SUFFIXES = (".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp")

SHAPE_TAGS = ["path", "line", "circle", "rect", "polyline", "polygon", "ellipse"]

# A TikZ "text box" is a decorative frame drawn around prose: a prompt
# template, a worked example, a callout. Below these thresholds the drawing is
# just the frame, and the text inside it is the point.
TEXT_BOX_MAX_SHAPES = 8
TEXT_BOX_MIN_CHARACTERS = 120

XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml"

_WHITESPACE = re.compile(r"\s+")
_HEADING = re.compile(r"^h[1-6]$")


def _text(node: Tag | None) -> str:
    return _WHITESPACE.sub(" ", node.get_text(" ", strip=True)).strip() if node else ""


def _classes(node: Tag) -> set[str]:
    value = node.get("class") or []
    return set(value if isinstance(value, list) else value.split())


def _element_children(node: Tag) -> list[Tag]:
    return [child for child in node.children if isinstance(child, Tag)]


def _inside_foreign_content(node: Tag) -> bool:
    """True for anything under <math> or <svg>, whose attributes are load-bearing."""
    for parent in node.parents:
        if isinstance(parent, Tag) and parent.name in ("math", "svg"):
            return True
    return False


class Transformer:
    """Builds a :class:`Book` from one fetched HTML rendering."""

    def __init__(
        self,
        source: HtmlSource,
        metadata: PaperMetadata,
        fetcher: Fetcher,
        math: MathRenderer,
        *,
        download_images: bool = True,
    ):
        self.source = source
        self.metadata = metadata
        self.fetcher = fetcher
        self.math = math
        self.download_images = download_images
        self.book = Book(metadata=metadata, source=source)
        self.soup = BeautifulSoup(source.html, "lxml")
        self._roots: list[Tag] = []
        self._image_cache: dict[str, str | None] = {}
        self._math_assets: dict[str, str] = {}
        self._footnote_count = 0

    # ------------------------------------------------------------------ build

    def build(self) -> Book:
        article = self._article()
        self._strip_chrome(article)
        abstract = self._extract_front_matter(article)

        self._add(*self._title_chapter(abstract))
        for index, section in enumerate(self._top_level_sections(article), start=1):
            self._add(*self._section_chapter(section, index))

        self._link_across_chapters()
        for chapter, root in zip(self.book.chapters, self._roots):
            chapter.body = serialize(root)
        return self.book

    def _add(self, chapter: Chapter, root: Tag) -> None:
        self.book.chapters.append(chapter)
        self._roots.append(root)

    def _article(self) -> Tag:
        article = self.soup.find("article", class_="ltx_document")
        if article is None:
            article = self.soup.find("div", class_="ltx_page_content")
        if article is None:
            article = self.soup.body
        if article is None:
            raise ValueError("the fetched page has no body")
        return article

    def _strip_chrome(self, article: Tag) -> None:
        for selector in CHROME_SELECTORS:
            for node in article.select(selector):
                node.decompose()
        for node in article.find_all(list(UNWRAP_TAGS)):
            node.unwrap()

    # --------------------------------------------------------------- helpers

    def _tag(self, name: str, *, text: str = "", **attributes: str) -> Tag:
        tag = self.soup.new_tag(name)
        for key, value in attributes.items():
            tag[key.rstrip("_").replace("_", "-")] = value
        if text:
            tag.string = text
        return tag

    # ----------------------------------------------------------- front matter

    def _extract_front_matter(self, article: Tag) -> Tag | None:
        """Remove the title block from the body and return the abstract."""
        for selector in (
            "h1.ltx_title_document",
            ".ltx_authors",
            ".ltx_dates",
            ".ltx_role_author",
            ".ltx_titlepage",
        ):
            for node in article.select(selector):
                node.decompose()

        abstract = article.find(class_="ltx_abstract")
        if abstract is not None:
            abstract.extract()
            for title in abstract.select(".ltx_title_abstract"):
                title.decompose()
        return abstract

    def _title_chapter(self, abstract: Tag | None) -> tuple[Chapter, Tag]:
        meta = self.metadata
        root = self._tag("section", class_="titlepage")
        root["epub:type"] = "titlepage"
        root.append(self._tag("h1", text=meta.title, class_="paper-title"))
        if meta.authors:
            root.append(self._tag("p", text="; ".join(meta.authors), class_="authors"))

        facts = [f"arXiv:{meta.arxiv_id.versioned}"]
        if meta.primary_category:
            facts.append(meta.primary_category)
        if meta.published:
            facts.append(meta.published.strftime("%d %B %Y"))
        root.append(self._tag("p", text=" · ".join(facts), class_="paper-facts"))

        abstract_section = self._tag("section", class_="abstract")
        abstract_section["epub:type"] = "abstract"
        abstract_section.append(self._tag("h2", text="Abstract"))
        if abstract is not None:
            self._process(abstract)
            for child in list(abstract.contents):
                abstract_section.append(child.extract())
        else:
            abstract_section.append(self._tag("p", text=meta.abstract))
        root.append(abstract_section)

        colophon = self._tag("section", class_="colophon")
        for label, value in (
            ("Comments.", meta.comment),
            ("Journal reference.", meta.journal_ref),
        ):
            if value:
                paragraph = self._tag("p")
                paragraph.append(self._tag("strong", text=label))
                paragraph.append(f" {value}")
                colophon.append(paragraph)
        if meta.doi:
            paragraph = self._tag("p")
            paragraph.append(self._tag("strong", text="DOI."))
            paragraph.append(" ")
            paragraph.append(
                self._tag("a", text=meta.doi, href=f"https://doi.org/{meta.doi}")
            )
            colophon.append(paragraph)

        note = self._tag("p", class_="source-note")
        note.append("Converted from the ")
        note.append(
            self._tag(
                "a", text=f"{self.source.provider} HTML rendering", href=self.source.url
            )
        )
        note.append(" of ")
        note.append(
            self._tag("a", text=meta.arxiv_id.abs_url, href=meta.arxiv_id.abs_url)
        )
        note.append(".")
        colophon.append(note)
        root.append(colophon)

        chapter = Chapter(
            identifier="titlepage",
            filename="titlepage.xhtml",
            title="Title & Abstract",
            body="",
            epub_type="titlepage",
        )
        return chapter, root

    # --------------------------------------------------------------- chapters

    def _top_level_sections(self, article: Tag) -> list[Tag]:
        sections = [
            child
            for child in article.find_all(["section", "div"], recursive=False)
            if _classes(child) & set(TOP_LEVEL_SECTION_CLASSES)
        ]
        if sections:
            return sections

        # Some conversions emit a flat document; keep it whole rather than
        # inventing structure that is not there.
        log.warning("no top-level sections found; emitting the paper as one chapter")
        return [article]

    def _section_chapter(self, section: Tag, index: int) -> tuple[Chapter, Tag]:
        self._process(section)
        heading = section.find(_HEADING)
        title = _text(heading) or f"Section {index}"
        self._demote_headings(section)
        toc = self._subsection_toc(section)

        identifier = f"ch{index:02d}"
        # No id on the wrapper: the section it holds already carries its own,
        # and repeating it would put a duplicate id in the file.
        root = self._tag("section", class_="chapter")
        section.extract()
        root.append(section)

        footnotes = self._collect_footnotes(root)
        if footnotes is not None:
            root.append(footnotes)

        classes = _classes(section)
        epub_type = ""
        if "ltx_bibliography" in classes:
            epub_type = "bibliography"
        elif "ltx_appendix" in classes:
            epub_type = "appendix"
        elif "ltx_acknowledgements" in classes:
            epub_type = "acknowledgments"

        chapter = Chapter(
            identifier=identifier,
            filename=f"{identifier}.xhtml",
            title=title,
            body="",
            toc=toc,
            epub_type=epub_type,
        )
        return chapter, root

    @staticmethod
    def _demote_headings(section: Tag) -> None:
        """Shift headings up one level so a section title becomes the file's h1."""
        for heading in section.find_all(_HEADING):
            level = int(heading.name[1])
            heading.name = f"h{max(1, level - 1)}"

    def _subsection_toc(self, section: Tag) -> list[TocEntry]:
        entries: list[TocEntry] = []
        for subsection in section.find_all("section"):
            if not _classes(subsection) & {"ltx_subsection", "ltx_appendix"}:
                continue
            title = _text(subsection.find(_HEADING))
            anchor = subsection.get("id")
            if title and anchor:
                entries.append(TocEntry(title=title, href=f"#{anchor}"))
        return entries

    # -------------------------------------------------------------- processing

    def _process(self, root: Tag) -> None:
        # Before the attribute clean, so text lifted out of a drawing is
        # tidied along with everything else.
        self._unwrap_svg_text_boxes(root)
        self._clean_attributes(root)
        self._namespace_svg(root)
        self._normalise_figures(root)
        self._flatten_equation_tables(root)
        self._rewrite_images(root)
        self._render_math(root)
        self._rewrite_footnotes(root)
        self._tidy_links(root)

    def _clean_attributes(self, root: Tag) -> None:
        for node in root.find_all(True):
            if node.name in ("math", "svg") or _inside_foreign_content(node):
                continue
            for attribute in list(node.attrs):
                if attribute in DROP_ATTRIBUTES or attribute.startswith(
                    DROP_ATTRIBUTE_PREFIXES
                ):
                    del node[attribute]

    def _unwrap_svg_text_boxes(self, root: Tag) -> None:
        """Lift prose out of decorative TikZ frames.

        LaTeXML puts the text of a framed block inside ``<foreignObject>``,
        which e-readers do not render at all -- the reader gets an empty grey
        box where a prompt template should be. When the surrounding drawing is
        only the frame, the text is worth more than the box.
        """
        for svg in root.find_all("svg"):
            objects = svg.find_all("foreignobject")
            if not objects:
                continue
            characters = sum(len(_text(node)) for node in objects)
            shapes = len(svg.find_all(SHAPE_TAGS))
            if characters < TEXT_BOX_MIN_CHARACTERS or shapes > TEXT_BOX_MAX_SHAPES:
                continue

            # A span, not a div: the drawing it replaces often sits inside an
            # inline wrapper, where a block element would be invalid. CSS
            # gives it block layout.
            replacement = self._tag("span", class_="text-box")
            for node in objects:
                for child in list(node.contents):
                    replacement.append(child.extract())
            svg.replace_with(replacement)

    @staticmethod
    def _namespace_svg(root: Tag) -> None:
        """Re-declare the namespaces an HTML parser dropped.

        Inline TikZ pictures arrive as ``<svg>``; without ``xmlns`` an EPUB
        reader parsing the file as XML sees an unknown HTML element instead.
        Content left inside a ``<foreignObject>`` needs the same treatment in
        reverse, since it is XHTML sitting inside an SVG subtree.
        """
        for node in root.find_all("svg"):
            if _inside_foreign_content(node):
                continue
            node["xmlns"] = SVG_NAMESPACE
            for embedded in node.find_all("foreignobject"):
                for child in _element_children(embedded):
                    child["xmlns"] = XHTML_NAMESPACE

    @staticmethod
    def _normalise_figures(root: Tag) -> None:
        """Keep at most one ``<figcaption>``, first or last, per figure.

        EPUB's schema only allows a caption at either end of a figure, but
        LaTeXML puts one after each panel of a multi-panel figure. Surplus
        captions become styled divs so the text survives without breaking the
        document.
        """
        for figure in root.find_all("figure"):
            children = _element_children(figure)
            captions = [child for child in children if child.name == "figcaption"]
            if len(captions) < 2 and (
                not captions or captions[0] in (children[0], children[-1])
            ):
                continue

            keep = captions[0] if captions[0] is children[0] else captions[-1]
            for caption in captions:
                if caption is keep:
                    continue
                caption.name = "div"
                caption["class"] = [*_classes(caption), "ltx_caption"]
            if keep is not _element_children(figure)[-1] and keep is not children[0]:
                figure.append(keep.extract())

    # LaTeXML lays every numbered equation out as a table, which reflows badly
    # on a small screen. A block with a floated number reads far better.
    def _flatten_equation_tables(self, root: Tag) -> None:
        for table in root.select("table.ltx_equation, table.ltx_eqn_table"):
            replacement = self._tag("div", class_="equation-group")
            if table.get("id"):
                replacement["id"] = table["id"]

            for row in table.find_all("tr") or [table]:
                line = self._tag("div", class_="equation")
                if row.get("id"):
                    line["id"] = row["id"]

                number: Tag | None = None
                for cell in row.find_all(["td", "th"]):
                    if "ltx_eqn_eqno" in _classes(cell):
                        number = cell
                        continue
                    for child in list(cell.contents):
                        line.append(child.extract())
                if number is not None and _text(number):
                    line.insert(0, self._tag("span", text=_text(number), class_="equation-number"))
                if _text(line) or line.find(["math", "img", "svg"]):
                    replacement.append(line)

            table.replace_with(replacement)

    def _render_math(self, root: Tag) -> None:
        for node in root.find_all("math"):
            latex = (node.get("alttext") or "").strip()
            try:
                rendered = self.math.render(node)
            except MathRenderError as exc:
                self.math.record_failure(latex, str(exc))
                self.book.warnings.append(f"could not render {latex!r}: {exc}")
                node.replace_with(
                    self._tag(
                        "code",
                        text=latex or "[unrenderable equation]",
                        class_="math-fallback",
                    )
                )
                continue

            classes = "math-display" if rendered.display else "math-inline"
            if self.math.inline_svg:
                element = parse_fragment(rendered.svg_markup)
                element["class"] = classes
                element["role"] = "math"
                if latex:
                    element["aria-label"] = latex
            else:
                element = self._tag(
                    "img",
                    src=self._math_asset(rendered),
                    alt=latex or "equation",
                    class_=classes,
                )

            style = f"width:{rendered.width_em}em;height:{rendered.height_em}em;"
            if not rendered.display:
                style += f"vertical-align:{-rendered.depth_em}em;"
            element["style"] = style
            node.replace_with(element)

    def _math_asset(self, rendered: RenderedMath) -> str:
        if rendered.key in self._math_assets:
            return self._math_assets[rendered.key]
        path = f"math/m{rendered.key}.{rendered.extension}"
        self.book.add_asset(
            Asset(path=path, data=rendered.data, media_type=rendered.media_type)
        )
        href = "../" + path
        self._math_assets[rendered.key] = href
        return href

    # LaTeXML inlines footnote text next to its marker. EPUB3 noteref/footnote
    # markup turns that into a tappable popup instead of a mid-sentence aside.
    def _rewrite_footnotes(self, root: Tag) -> None:
        for note in root.select("span.ltx_note, .ltx_role_footnote"):
            content = note.find(class_="ltx_note_content")
            if content is None:
                continue
            for stray in content.select(".ltx_note_mark, .ltx_note_type"):
                stray.decompose()

            self._footnote_count += 1
            number = self._footnote_count
            mark = _text(note.find(class_="ltx_note_mark")) or str(number)

            aside = self._tag("aside", class_="footnote")
            aside["id"] = f"fn{number}"
            aside["epub:type"] = "footnote"
            paragraph = self._tag("p")
            paragraph.append(
                self._tag(
                    "a", text=f"{mark}. ", href=f"#fnref{number}", class_="footnote-back"
                )
            )
            for child in list(content.contents):
                paragraph.append(child.extract())
            aside.append(paragraph)

            reference = self._tag("a", href=f"#fn{number}", class_="noteref")
            reference["id"] = f"fnref{number}"
            reference["epub:type"] = "noteref"
            reference.append(self._tag("sup", text=mark))

            note.replace_with(reference)
            # Parked at the end of the chapter by _collect_footnotes.
            root.append(aside)

    def _collect_footnotes(self, root: Tag) -> Tag | None:
        asides = root.select("aside.footnote")
        if not asides:
            return None
        section = self._tag("section", class_="footnotes")
        section["epub:type"] = "footnotes"
        section.append(self._tag("h2", text="Notes"))
        for aside in asides:
            section.append(aside.extract())
        return section

    # ----------------------------------------------------------------- images

    def _rewrite_images(self, root: Tag) -> None:
        self._normalise_embedded_graphics(root)
        for image in root.find_all("img"):
            source_url = (image.get("src") or "").strip()
            # arXiv sprinkles tiny base64 sprites through its chrome; a real
            # figure is always a fetchable file.
            if not source_url or source_url.startswith("data:"):
                image.decompose()
                continue

            path = self._fetch_image(urljoin(self.source.url, source_url))
            if path is None:
                image.decompose()
                continue
            alt = image.get("alt", "")
            image.attrs = {"src": "../" + path, "alt": alt, "class": "figure-image"}

    def _normalise_embedded_graphics(self, root: Tag) -> None:
        """Turn LaTeXML's ``<object>`` graphics into plain images.

        Vector figures come through as ``<object type="image/svg+xml">``, which
        e-readers do not render; the same file works fine behind an ``<img>``.
        """
        for node in root.find_all(["object", "embed"]):
            location = (node.get("data") or node.get("src") or "").strip()
            declared_type = (node.get("type") or "").lower()
            is_image = declared_type.startswith("image/") or location.lower().endswith(
                IMAGE_SUFFIXES
            )
            if not (location and is_image):
                node.decompose()
                continue
            node.replace_with(
                self._tag("img", src=location, alt=_text(node) or node.get("id", ""))
            )

    def _fetch_image(self, url: str) -> str | None:
        if url in self._image_cache:
            return self._image_cache[url]

        result: str | None = None
        if self.download_images:
            data = self.fetcher.try_get_bytes(url)
            if data:
                prepared = prepare_image(data, url)
                name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
                stem = re.sub(r"[^a-zA-Z0-9_-]", "-", name).rsplit(".", 1)[0][:60]
                path = (
                    f"images/{len(self._image_cache):03d}-{stem or 'figure'}"
                    f".{prepared.extension}"
                )
                self.book.add_asset(
                    Asset(path=path, data=prepared.data, media_type=prepared.media_type)
                )
                result = path
            else:
                self.book.warnings.append(f"figure could not be downloaded: {url}")

        self._image_cache[url] = result
        return result

    # ------------------------------------------------------------------ links

    def _tidy_links(self, root: Tag) -> None:
        for link in root.find_all("a"):
            href = (link.get("href") or "").strip()
            # data: and javascript: URLs are arXiv's own widgets, and EPUB
            # forbids the former outright.
            if not href or href.startswith(("javascript:", "data:")):
                link.unwrap()
                continue
            if href.startswith("#"):
                continue
            link["href"] = urljoin(self.source.url, href)

    def _link_across_chapters(self) -> None:
        """Point in-document anchors at whichever file ended up holding them."""
        location: dict[str, str] = {}
        for chapter, root in zip(self.book.chapters, self._roots):
            for node in [root, *root.find_all(id=True)]:
                if node.get("id"):
                    location.setdefault(node["id"], chapter.filename)

        for chapter, root in zip(self.book.chapters, self._roots):
            for link in root.find_all("a", href=True):
                href = link["href"]
                if not href.startswith("#"):
                    continue
                target = href[1:]
                filename = location.get(target)
                if filename is None:
                    # The anchor was dropped with the chrome it lived in.
                    link.unwrap()
                elif filename != chapter.filename:
                    link["href"] = f"{filename}#{target}"

            chapter.toc = [
                TocEntry(entry.title, f"{chapter.filename}{entry.href}")
                for entry in chapter.toc
            ]


def transform(
    source: HtmlSource,
    metadata: PaperMetadata,
    fetcher: Fetcher,
    math: MathRenderer,
    *,
    download_images: bool = True,
) -> Book:
    return Transformer(
        source, metadata, fetcher, math, download_images=download_images
    ).build()
