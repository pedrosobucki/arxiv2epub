"""The polling loop: inbox in, Kindle out.

Runs unattended on a home server, so the behaviour under failure matters as
much as the happy path. Every message is answered -- a converted paper, a
duplicate notice, or an explanation of what went wrong -- and only then marked
read. A message the worker never got to stays unread and is retried on the
next pass; a message it definitively handled is never processed twice.
"""

from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from ..ids import ArxivId, find_in_text
from ..pipeline import Options, Result, build_epub
from ..sources import NoHtmlAvailable
from .config import ServiceConfig
from .mailbox import IncomingMessage, Mailbox

log = logging.getLogger(__name__)

MEGABYTE = 1024 * 1024


@dataclass
class Outcome:
    """What the worker decided about one message."""

    action: str
    detail: str = ""

    def __str__(self) -> str:  # pragma: no cover - logging convenience
        return f"{self.action}: {self.detail}" if self.detail else self.action


class Worker:
    """Watches one mailbox and converts whatever arrives."""

    def __init__(
        self,
        config: ServiceConfig,
        mailbox: Mailbox | None = None,
        convert=build_epub,
    ):
        self.config = config
        self.mailbox = mailbox if mailbox is not None else Mailbox(config)
        self.convert = convert
        self._running = True
        # Mail from an unknown sender is left unread, so it comes back on
        # every pass; remembering it keeps a months-long log readable.
        self._ignored: set[int] = set()

    # ------------------------------------------------------------- one pass

    def poll(self) -> list[Outcome]:
        """Process everything currently unread. Never raises."""
        try:
            messages = self.mailbox.fetch_unseen()
        except Exception as exc:  # noqa: BLE001 - the loop must survive the network
            log.error("could not read the inbox: %s", exc)
            log.debug("inbox failure", exc_info=True)
            return []

        if not messages:
            return []

        log.info("%d unread message(s)", len(messages))
        outcomes: list[Outcome] = []
        for message in messages:
            try:
                outcome = self.handle(message)
            except Exception as exc:  # noqa: BLE001 - one bad message is not fatal
                log.exception("unhandled error on message %s", message.uid)
                outcome = Outcome("error", str(exc))
            outcomes.append(outcome)
            log.info("message %s -> %s", message.uid, outcome)
        return outcomes

    def handle(self, message: IncomingMessage) -> Outcome:
        """Deal with one message and mark it read once it is settled."""
        if not self.config.allows(message.sender):
            # Left unread deliberately: an unknown sender is not this worker's
            # mail, and silently consuming it would hide it from the human.
            if message.uid not in self._ignored:
                self._ignored.add(message.uid)
                log.warning(
                    "ignoring message from %s", message.sender or "(no sender)"
                )
            return Outcome("ignored", f"sender not allowed: {message.sender}")

        reference = find_in_text(message.searchable)
        if reference is None:
            self._reply(
                message,
                "No arXiv link found",
                "I could not find an arXiv link or id in that message.\n\n"
                "Send something like https://arxiv.org/abs/1706.03762 "
                "in the subject or the body.",
            )
            self._settle(message)
            return Outcome("no-reference")

        existing = self._already_converted(reference)
        if existing is not None:
            self._reply(
                message,
                f"Already sent: {reference.bare}",
                f'"{existing.stem}" was converted and sent previously.\n\n'
                "Delete it from the output folder if you want it rebuilt.",
            )
            self._settle(message)
            return Outcome("duplicate", str(reference))

        try:
            result = self._convert(reference)
        except NoHtmlAvailable as exc:
            self._reply(
                message,
                f"Could not convert {reference.bare}",
                f"{exc}\n\nThis usually means neither arXiv nor ar5iv has an "
                "HTML rendering of the paper, which is common for older "
                "submissions.",
            )
            self._settle(message)
            return Outcome("unconvertible", str(reference))
        except Exception as exc:  # noqa: BLE001 - report, do not retry forever
            log.exception("conversion of %s failed", reference)
            self._reply(
                message,
                f"Could not convert {reference.bare}",
                f"The conversion failed: {exc}",
            )
            self._settle(message)
            return Outcome("failed", f"{reference}: {exc}")

        size_mb = result.size_bytes / MEGABYTE
        if size_mb > self.config.max_attachment_mb:
            self._reply(
                message,
                f"Too large to send: {reference.bare}",
                f"The EPUB came to {size_mb:.1f} MB, over the "
                f"{self.config.max_attachment_mb:g} MB limit. It is on the "
                f"server at {result.path}.",
            )
            self._settle(message)
            return Outcome("too-large", f"{size_mb:.1f} MB")

        title = result.book.metadata.title
        self.mailbox.send(
            to=self.config.kindle_email,
            subject=title,
            body=" ",
            attachment=result.path,
        )
        log.info("delivered %r to %s", title, self.config.kindle_email)

        note = ""
        if result.warnings:
            note = "\n\nConverted with warnings:\n" + "\n".join(
                f"  - {warning}" for warning in result.warnings[:10]
            )
        self._reply(
            message,
            f"Sent: {title}",
            f'"{title}"\n{result.book.metadata.author_line}\n'
            f"arXiv:{result.book.metadata.arxiv_id.versioned}\n\n"
            f"Sent to {self.config.kindle_email} ({size_mb:.1f} MB).{note}",
        )
        self._settle(message)
        return Outcome("sent", title)

    # ---------------------------------------------------------------- pieces

    def _convert(self, reference: ArxivId) -> Result:
        return self.convert(
            reference.versioned,
            self.config.output_dir,
            Options(
                math_format=self.config.math_format,
                cache_dir=self.config.cache_dir,
            ),
        )

    def _already_converted(self, reference: ArxivId) -> Path | None:
        """Look for a previous conversion of the same paper.

        Matched on the bare id, so re-sending the same link is recognised as a
        repeat rather than silently producing a second copy.
        """
        directory = self.config.output_dir
        if not directory.is_dir():
            return None
        needle = reference.bare.replace("/", "_")
        return next(
            (path for path in sorted(directory.glob("*.epub")) if needle in path.name),
            None,
        )

    def _reply(self, message: IncomingMessage, subject: str, body: str) -> None:
        try:
            self.mailbox.send(to=message.sender, subject=subject, body=body)
        except Exception as exc:  # noqa: BLE001 - a failed reply is not fatal
            log.error("could not reply to %s: %s", message.sender, exc)

    def _settle(self, message: IncomingMessage) -> None:
        """Mark a message read now that it has been answered."""
        try:
            self.mailbox.mark_seen(message.uid)
        except Exception as exc:  # noqa: BLE001
            log.error("could not mark message %s read: %s", message.uid, exc)

    # ------------------------------------------------------------------ loop

    def stop(self, *_: object) -> None:
        log.info("shutting down after the current pass")
        self._running = False

    def run(self) -> None:
        """Poll until told to stop."""
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)

        log.info("arxiv2epub worker started")
        log.info(self.config.describe())
        if self.config.dry_run:
            log.warning("DRY_RUN is set: nothing will actually be emailed")

        while self._running:
            self.poll()
            # Sleep in slices so a stop signal is acted on promptly rather
            # than after a full polling interval.
            deadline = time.monotonic() + self.config.poll_interval_seconds
            while self._running and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))
        log.info("worker stopped")
