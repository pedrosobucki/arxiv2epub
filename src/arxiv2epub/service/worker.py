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
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ..ids import ArxivId, find_all_in_text
from ..pipeline import Options, Result, build_epub
from ..sources import NoHtmlAvailable
from .config import ServiceConfig
from .mailbox import IncomingMessage, Mailbox

log = logging.getLogger(__name__)

MEGABYTE = 1024 * 1024


@contextmanager
def _nothing():
    """Stand-in for a mailbox that does not pool connections."""
    yield


# One wording for each outcome, used in both the subject and the body so a
# reply reads the same way whichever part of it you look at.
LABELS = {
    "sent": ("Sent", "sent"),
    "duplicate": ("Already sent", "already sent"),
    "unconvertible": ("Could not convert", "could not convert"),
    "failed": ("Failed", "failed"),
    "too-large": ("Too large to send", "too large to mail"),
}


@dataclass
class Item:
    """What became of one paper referenced in a message."""

    reference: ArxivId
    status: str
    detail: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def heading(self) -> str:
        return LABELS.get(self.status, (self.status.capitalize(), ""))[0]

    def line(self) -> str:
        label = LABELS.get(self.status, ("", self.status))[1]
        suffix = f" - {self.detail}" if self.detail else ""
        text = f"  [{label}] arXiv:{self.reference.bare}{suffix}"
        # The reader can only act on a warning they can actually read, so the
        # text goes in rather than a count of them.
        for warning in self.warnings[:5]:
            text += f"\n      warning: {warning}"
        if len(self.warnings) > 5:
            text += f"\n      ... and {len(self.warnings) - 5} more warnings"
        return text


@dataclass
class Outcome:
    """What the worker decided about one message."""

    action: str
    detail: str = ""

    def __str__(self) -> str:  # pragma: no cover - logging convenience
        return f"{self.action}: {self.detail}" if self.detail else self.action

    @classmethod
    def summarise(cls, items: list[Item]) -> "Outcome":
        """One message may carry several papers; report them as a whole."""
        if len(items) == 1:
            return cls(items[0].status, items[0].detail or str(items[0].reference))
        tally: dict[str, int] = {}
        for item in items:
            tally[item.status] = tally.get(item.status, 0) + 1
        return cls(
            "batch",
            ", ".join(f"{count} {status}" for status, count in tally.items()),
        )


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
        """Process everything currently unread. Never raises.

        The whole pass runs inside one mail session, so a busy inbox costs one
        login rather than one per message -- providers throttle accounts that
        reconnect constantly.
        """
        session = getattr(self.mailbox, "session", None)
        with session() if session else _nothing():
            try:
                messages = self.mailbox.fetch_unseen()
            except Exception as exc:  # noqa: BLE001 - the loop survives the network
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
        """Deal with one message and mark it read once it is settled.

        A message may name several papers; each is converted and delivered on
        its own, and the sender gets one reply covering the lot rather than an
        inbox full of confirmations.
        """
        if not self.config.allows(message.sender):
            # Left unread deliberately: an unknown sender is not this worker's
            # mail, and silently consuming it would hide it from the human.
            if message.uid not in self._ignored:
                self._ignored.add(message.uid)
                log.warning(
                    "ignoring message from %s", message.sender or "(no sender)"
                )
            return Outcome("ignored", f"sender not allowed: {message.sender}")

        references = find_all_in_text(message.searchable)
        if not references:
            self._reply(
                message,
                "No arXiv link found",
                "I could not find an arXiv link or id in that message.\n\n"
                "Send something like https://arxiv.org/abs/1706.03762 in the "
                "subject or the body. Several links in one message is fine.",
            )
            self._settle(message)
            return Outcome("no-reference")

        # A forwarded newsletter can carry dozens of links; converting all of
        # them unprompted would flood the Kindle and the mail server.
        dropped: list[ArxivId] = []
        if len(references) > self.config.max_links_per_email:
            dropped = references[self.config.max_links_per_email :]
            references = references[: self.config.max_links_per_email]

        log.info(
            "message %s references %d paper(s)", message.uid, len(references)
        )
        items = [self._process(reference) for reference in references]

        self._reply(message, *self._summary(items, dropped))
        self._settle(message)
        return Outcome.summarise(items)

    def _process(self, reference: ArxivId) -> Item:
        """Convert and deliver one paper, reporting rather than raising."""
        existing = self._already_converted(reference)
        if existing is not None:
            return Item(reference, "duplicate", existing.stem)

        try:
            result = self._convert(reference)
        except NoHtmlAvailable as exc:
            log.warning("no HTML rendering for %s: %s", reference, exc)
            return Item(
                reference,
                "unconvertible",
                "neither arXiv nor ar5iv has an HTML rendering",
            )
        except Exception as exc:  # noqa: BLE001 - report, do not retry forever
            log.exception("conversion of %s failed", reference)
            return Item(reference, "failed", str(exc))

        size_mb = result.size_bytes / MEGABYTE
        title = result.book.metadata.title
        if size_mb > self.config.max_attachment_mb:
            return Item(
                reference,
                "too-large",
                f"{size_mb:.1f} MB, left on the server at {result.path}",
            )

        try:
            self.mailbox.send(
                to=self.config.kindle_email,
                subject=title,
                body=" ",
                attachment=result.path,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("could not deliver %s", reference)
            return Item(reference, "failed", f"delivery failed: {exc}")

        log.info("delivered %r to %s", title, self.config.kindle_email)
        return Item(reference, "sent", title, warnings=list(result.warnings))

    def _summary(
        self, items: list[Item], dropped: list[ArxivId]
    ) -> tuple[str, str]:
        """Compose the single reply that covers everything in one message."""
        sent = [item for item in items if item.status == "sent"]
        if len(items) == 1:
            item = items[0]
            trailer = item.detail if item.status == "sent" else item.reference.bare
            subject = f"{item.heading}: {trailer}"
        else:
            subject = f"{len(sent)} of {len(items)} papers sent to your Kindle"

        lines = [item.line() for item in items]
        body = "\n".join(lines)
        if sent:
            body += f"\n\nDelivered to {self.config.kindle_email}."
        if dropped:
            body += (
                f"\n\n{len(dropped)} further link(s) in that message were not "
                f"converted, over the {self.config.max_links_per_email}-per-message "
                "limit:\n"
                + "\n".join(f"  arXiv:{reference.bare}" for reference in dropped)
            )
        return subject, body

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
            self.mailbox.send(
                to=message.sender,
                subject=subject,
                body=body,
                in_reply_to=message.message_id,
            )
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
