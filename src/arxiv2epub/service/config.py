"""Service configuration, read from the environment.

The variable names are inherited verbatim from the previous arxiv2kindle
worker so that an existing ``.env`` keeps working untouched. ``CHROME_PATH`` is
accepted and ignored: this version renders from arXiv's HTML rather than
driving a headless browser, but rejecting the key would break that promise.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# A forwarded digest can name dozens of papers. Converting every one of them
# unprompted would flood both the Kindle and the mail server.
DEFAULT_MAX_LINKS_PER_EMAIL = 10

# Send-to-Kindle rejects oversized attachments, and most SMTP servers refuse
# them well before that. Converted papers are ~1 MB, so anything near this is a
# sign something has gone wrong.
DEFAULT_MAX_ATTACHMENT_MB = 25.0

# Kept only so an inherited .env does not fail validation.
IGNORED_KEYS = ("CHROME_PATH",)


class ConfigError(RuntimeError):
    """Raised when the environment is missing something the worker needs."""


@dataclass(frozen=True)
class ServiceConfig:
    """Everything the worker needs to run."""

    email_user: str
    email_password: str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    kindle_email: str
    allowed_senders: tuple[str, ...]
    poll_interval_seconds: float
    output_dir: Path
    cache_dir: Path | None = None
    math_format: str = "svg"
    max_attachment_mb: float = DEFAULT_MAX_ATTACHMENT_MB
    max_links_per_email: int = DEFAULT_MAX_LINKS_PER_EMAIL
    mailbox: str = "INBOX"
    dry_run: bool = False
    smtp_allow_cleartext: bool = False
    ignored_keys: tuple[str, ...] = field(default=())

    @property
    def smtp_use_ssl(self) -> bool:
        """Port 465 is implicit TLS; everything else negotiates STARTTLS."""
        return self.smtp_port == 465

    def allows(self, address: str) -> bool:
        return address.strip().lower() in self.allowed_senders

    def describe(self) -> str:
        """A one-line summary safe to log: no password, no secrets."""
        return (
            f"user={self.email_user} imap={self.imap_host}:{self.imap_port} "
            f"smtp={self.smtp_host}:{self.smtp_port} kindle={self.kindle_email} "
            f"senders={len(self.allowed_senders)} "
            f"poll={self.poll_interval_seconds:g}s out={self.output_dir}"
        )


def _required(key: str) -> str:
    value = (os.environ.get(key) or "").strip()
    if not value:
        raise ConfigError(f"missing required environment variable: {key}")
    return value


def _flag(key: str) -> bool:
    return (os.environ.get(key) or "").strip().lower() in ("1", "true", "yes", "on")


def _number(key: str, default: float) -> float:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from None


def load_config(env_file: Path | str | None = None) -> ServiceConfig:
    """Build the configuration, optionally loading a .env file first.

    In Docker the variables arrive through compose's ``env_file``; loading one
    here as well is what makes ``arxiv2epub-worker`` runnable straight from a
    checkout.
    """
    if env_file is not None:
        from dotenv import load_dotenv

        path = Path(env_file)
        if not path.exists():
            raise ConfigError(f"no such env file: {path}")
        load_dotenv(path, override=False)

    senders = tuple(
        address.strip().lower()
        for address in _required("ALLOWED_SENDERS").split(",")
        if address.strip()
    )
    if not senders:
        raise ConfigError("ALLOWED_SENDERS is empty; the worker would ignore all mail")

    # The previous worker expressed the interval in milliseconds.
    poll_seconds = _number("POLL_INTERVAL_MS", 30_000.0) / 1000.0
    if poll_seconds < 5:
        raise ConfigError("POLL_INTERVAL_MS below 5000 would hammer the mail server")

    present = tuple(key for key in IGNORED_KEYS if os.environ.get(key))
    if present:
        log.debug("ignoring inherited setting(s): %s", ", ".join(present))

    return ServiceConfig(
        email_user=_required("EMAIL_USER"),
        email_password=_required("EMAIL_PASSWORD"),
        imap_host=_required("EMAIL_IMAP_HOST"),
        imap_port=int(_number("EMAIL_IMAP_PORT", 993)),
        smtp_host=_required("EMAIL_SMTP_HOST"),
        smtp_port=int(_number("EMAIL_SMTP_PORT", 587)),
        kindle_email=_required("KINDLE_EMAIL"),
        allowed_senders=senders,
        poll_interval_seconds=poll_seconds,
        output_dir=Path(os.environ.get("OUTPUT_DIR") or "/out"),
        cache_dir=Path(os.environ["CACHE_DIR"]) if os.environ.get("CACHE_DIR") else None,
        math_format=(os.environ.get("MATH_FORMAT") or "svg").strip(),
        max_attachment_mb=_number("MAX_ATTACHMENT_MB", DEFAULT_MAX_ATTACHMENT_MB),
        max_links_per_email=max(
            1, int(_number("MAX_LINKS_PER_EMAIL", DEFAULT_MAX_LINKS_PER_EMAIL))
        ),
        mailbox=(os.environ.get("MAILBOX") or "INBOX").strip(),
        dry_run=_flag("DRY_RUN"),
        smtp_allow_cleartext=_flag("SMTP_ALLOW_CLEARTEXT"),
        ignored_keys=present,
    )
