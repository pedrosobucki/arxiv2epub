"""A small, polite HTTP layer with retries and an on-disk cache.

arXiv asks tools to identify themselves and to avoid hammering the site, so
every request in this project goes through here.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import __version__

log = logging.getLogger(__name__)

USER_AGENT = (
    f"arxiv2epub/{__version__} (+https://github.com/arxiv2epub; "
    "converts arXiv papers to EPUB for personal reading)"
)

# arXiv's robots.txt asks automated clients for a 15 second gap; we are far
# gentler than that per host but still leave a courtesy delay between hits.
_MIN_INTERVAL_SECONDS = 1.0


class Fetcher:
    """Fetches URLs, optionally caching bodies on disk between runs."""

    def __init__(self, cache_dir: Path | None = None, timeout: float = 60.0):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self._last_request_at = 0.0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        retry = Retry(
            total=4,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "HEAD"]),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=8)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        return self.cache_dir / digest

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < _MIN_INTERVAL_SECONDS:
            time.sleep(_MIN_INTERVAL_SECONDS - elapsed)
        self._last_request_at = time.monotonic()

    def get(self, url: str, *, allow_cache: bool = True) -> tuple[bytes, str]:
        """Fetch a URL, returning its body and the URL it finally resolved to.

        The resolved URL matters because relative asset paths in the fetched
        page have to be joined against wherever the redirects landed, not
        against what we asked for.
        """
        cache_path = self._cache_path(url) if allow_cache else None
        final_url_path = cache_path.with_suffix(".url") if cache_path else None
        if cache_path and cache_path.exists():
            log.debug("cache hit %s", url)
            resolved = (
                final_url_path.read_text().strip()
                if final_url_path and final_url_path.exists()
                else url
            )
            return cache_path.read_bytes(), resolved

        self._throttle()
        log.debug("GET %s", url)
        response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
        response.raise_for_status()
        body = response.content
        if cache_path:
            cache_path.write_bytes(body)
            if final_url_path:
                final_url_path.write_text(response.url)
        return body, response.url

    def get_bytes(self, url: str, *, allow_cache: bool = True) -> bytes:
        """Fetch a URL, raising for any non-2xx status."""
        return self.get(url, allow_cache=allow_cache)[0]

    def get_text(self, url: str, *, allow_cache: bool = True) -> str:
        body = self.get_bytes(url, allow_cache=allow_cache)
        return body.decode("utf-8", errors="replace")

    def try_get_bytes(self, url: str, *, allow_cache: bool = True) -> bytes | None:
        """Fetch a URL, returning None instead of raising on failure."""
        try:
            return self.get_bytes(url, allow_cache=allow_cache)
        except Exception as exc:  # noqa: BLE001 - any failure means "no content"
            log.debug("fetch failed for %s: %s", url, exc)
            return None
