"""Parsing and normalising arXiv identifiers."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Post-2007 identifiers: 0704.0001, 1706.03762, 2501.12948. The first four
# digits are YYMM, so requiring a real month keeps things like a "2024.0315"
# build number in an email from being mistaken for a paper.
_MODERN = re.compile(r"(?P<num>\d{2}(?:0[1-9]|1[0-2])\.\d{4,5})(?:v(?P<ver>\d+))?")
# Pre-2007 identifiers: math.GT/0309136, hep-th/9901001.
_LEGACY = re.compile(
    r"(?P<num>[a-z][a-z-]*(?:\.[A-Z]{2})?/\d{7})(?:v(?P<ver>\d+))?", re.IGNORECASE
)


# Ordered from most to least certain. A bare "2024.0315" in an email footer
# looks exactly like an arXiv id, so an explicit URL or "arXiv:" prefix is
# trusted first and the bare form is only a last resort.
_QUALIFIED = (
    re.compile(r"ar(?:xiv|5iv)[^\s]*?/(?:abs|html|pdf)/([^\s<>\"')\]]+)", re.IGNORECASE),
    re.compile(r"arxiv[:\s]\s*([^\s<>\"')\]]+)", re.IGNORECASE),
    re.compile(r"10\.48550/arxiv\.([^\s<>\"')\]]+)", re.IGNORECASE),
)


class NotAnArxivReference(ValueError):
    """Raised when the input cannot be read as an arXiv paper reference."""


@dataclass(frozen=True)
class ArxivId:
    """An arXiv identifier, optionally pinned to a specific version."""

    number: str
    version: int | None = None

    @property
    def bare(self) -> str:
        """The identifier without any version suffix."""
        return self.number

    @property
    def versioned(self) -> str:
        """The identifier including its version suffix, when one is known."""
        return f"{self.number}v{self.version}" if self.version else self.number

    @property
    def slug(self) -> str:
        """A filesystem-safe form of the identifier."""
        return self.versioned.replace("/", "_")

    @property
    def abs_url(self) -> str:
        return f"https://arxiv.org/abs/{self.versioned}"

    def with_version(self, version: int | None) -> "ArxivId":
        return ArxivId(self.number, version)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.versioned


def parse(reference: str) -> ArxivId:
    """Read an arXiv id out of a bare id, an abs/pdf/html URL, or a DOI.

    Accepts the shapes people actually paste: ``1706.03762``, ``arXiv:1706.03762v7``,
    ``https://arxiv.org/abs/1706.03762``, ``.../pdf/1706.03762v7.pdf``,
    ``ar5iv.org/abs/...``, ``https://doi.org/10.48550/arXiv.2501.12948``.
    """
    if not reference or not reference.strip():
        raise NotAnArxivReference("empty arXiv reference")

    text = reference.strip()
    # A trailing ".pdf" would otherwise be mistaken for part of a legacy id.
    text = re.sub(r"\.pdf$", "", text, flags=re.IGNORECASE)

    for pattern in (_MODERN, _LEGACY):
        match = pattern.search(text)
        if match:
            version = match.group("ver")
            return ArxivId(match.group("num"), int(version) if version else None)

    raise NotAnArxivReference(f"could not find an arXiv id in {reference!r}")


def find_in_text(text: str) -> ArxivId | None:
    """Pick an arXiv reference out of free-form prose, or return None.

    Used on incoming mail, where the id is surrounded by whatever the sender
    (or their mail client) wrapped around it.
    """
    if not text:
        return None

    for pattern in _QUALIFIED:
        for match in pattern.finditer(text):
            try:
                return parse(match.group(1))
            except NotAnArxivReference:
                continue
    try:
        return parse(text)
    except NotAnArxivReference:
        return None
