"""The intermediate representation between parsing and EPUB writing."""

from __future__ import annotations

from dataclasses import dataclass, field

from .metadata import PaperMetadata
from .sources import HtmlSource


@dataclass
class Asset:
    """A file that ships inside the EPUB alongside the text."""

    path: str
    data: bytes
    media_type: str
    properties: str = ""

    @property
    def manifest_id(self) -> str:
        return "asset-" + self.path.replace("/", "-").replace(".", "-")


@dataclass
class TocEntry:
    """One line in the table of contents."""

    title: str
    href: str
    children: list["TocEntry"] = field(default_factory=list)


@dataclass
class Chapter:
    """One XHTML file in the reading order."""

    identifier: str
    filename: str
    title: str
    body: str
    toc: list[TocEntry] = field(default_factory=list)
    in_toc: bool = True
    epub_type: str = ""
    properties: str = ""


@dataclass
class Book:
    """Everything needed to write the EPUB."""

    metadata: PaperMetadata
    source: HtmlSource
    chapters: list[Chapter] = field(default_factory=list)
    assets: list[Asset] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_asset(self, asset: Asset) -> Asset:
        self.assets.append(asset)
        return asset
