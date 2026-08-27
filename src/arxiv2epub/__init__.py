"""Turn an arXiv link into a well-formatted, Kindle-ready EPUB."""

__version__ = "0.1.0"

from .pipeline import build_epub

__all__ = ["build_epub", "__version__"]
