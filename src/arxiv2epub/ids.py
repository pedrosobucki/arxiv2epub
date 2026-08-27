"""Parsing and normalising arXiv identifiers."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Post-2007 identifiers: 0704.0001, 1706.03762, 2501.12948 (4 or 5 digit sequence).
_MODERN = re.compile(r"(?P<num>\d{4}\.\d{4,5})(?:v(?P<ver>\d+))?")
# Pre-2007 identifiers: math.GT/0309136, hep-th/9901001.
_LEGACY = re.compile(
    r"(?P<num>[a-z][a-z-]*(?:\.[A-Z]{2})?/\d{7})(?:v(?P<ver>\d+))?", re.IGNORECASE
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
