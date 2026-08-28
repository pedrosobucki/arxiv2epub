"""The mail-in worker: watch an inbox, convert, deliver to a Kindle."""

from .config import ServiceConfig, load_config

__all__ = ["ServiceConfig", "load_config"]
