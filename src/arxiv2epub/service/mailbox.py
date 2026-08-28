"""Reading the inbox and sending the results out again.

Two decisions differ from the worker this replaces. Messages are fetched with
``BODY.PEEK[]`` so the server does not mark them read behind our back -- the
worker sets ``\\Seen`` itself, only once a message has actually been dealt
with, so a crash mid-conversion leaves the request to be retried. And bodies
are parsed as real MIME rather than by reaching for part ``1``, because mail
from a phone arrives as multipart HTML and the link lives wherever the client
decided to put it.
"""

from __future__ import annotations

import email.message
import email.policy
import logging
import smtplib
import ssl
from contextlib import contextmanager
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import Iterator

from imapclient import SEEN, IMAPClient

from .config import ServiceConfig

log = logging.getLogger(__name__)

EPUB_MEDIA_TYPE = ("application", "epub+zip")

# A stalled socket in a service that runs for months is a hang, not an error,
# so every network call gets a deadline.
NETWORK_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class IncomingMessage:
    """One message waiting in the inbox."""

    uid: int
    sender: str
    subject: str
    body: str
    message_id: str = ""

    @property
    def searchable(self) -> str:
        """Subject first, then body: a link in the subject is the clearer ask."""
        return f"{self.subject}\n{self.body}"


def _part_text(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return str(payload or "")
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:  # a charset Python does not know
        return payload.decode("utf-8", errors="replace")


def _extract_body(message: email.message.Message) -> str:
    """Gather every readable text part of a message.

    Deliberately not ``get_body``, which picks one alternative: a phone often
    puts "Sent from my iPhone" in the plain part and the actual link only in
    the HTML one, so choosing either in advance loses the link. Collecting all
    of them and searching the lot cannot.
    """
    chunks: list[str] = []
    for part in message.walk():
        if part.get_content_maintype() != "text":
            continue
        if part.get_content_disposition() == "attachment":
            continue
        text = _part_text(part).strip()
        if text:
            chunks.append(text)
    return "\n".join(chunks)


class Mailbox:
    """IMAP and SMTP access for one account."""

    def __init__(self, config: ServiceConfig, ssl_context: ssl.SSLContext | None = None):
        self.config = config
        # Overridable so a server with a private CA (or a test double) can be
        # reached without loosening anything by default.
        self.ssl_context = ssl_context
        self._pooled = False
        self._imap_conn: IMAPClient | None = None
        self._smtp_conn: smtplib.SMTP | None = None

    # Providers throttle accounts that reconnect constantly, and Zoho is
    # stricter than most. Holding one connection open for a whole pass turns
    # "one login per message" into "one login per pass".
    @contextmanager
    def session(self) -> Iterator["Mailbox"]:
        """Reuse one IMAP and one SMTP connection for everything inside."""
        self._pooled = True
        try:
            yield self
        finally:
            self._pooled = False
            self._drop_imap()
            self._drop_smtp()

    def _connect_imap(self) -> IMAPClient:
        client = IMAPClient(
            host=self.config.imap_host,
            port=self.config.imap_port,
            ssl=True,
            ssl_context=self.ssl_context,
            timeout=NETWORK_TIMEOUT_SECONDS,
        )
        client.login(self.config.email_user, self.config.email_password)
        return client

    def _drop_imap(self) -> None:
        client, self._imap_conn = self._imap_conn, None
        if client is None:
            return
        try:
            client.logout()
        except Exception:  # noqa: BLE001 - a failed logout must not mask work
            log.debug("IMAP logout failed", exc_info=True)

    @contextmanager
    def _imap(self) -> Iterator[IMAPClient]:
        if not self._pooled:
            client = self._connect_imap()
            try:
                yield client
            finally:
                self._imap_conn = client
                self._drop_imap()
            return

        if self._imap_conn is None:
            self._imap_conn = self._connect_imap()
        try:
            yield self._imap_conn
        except Exception:
            # A pooled connection that has errored may be unusable; drop it so
            # the next call starts clean rather than compounding the failure.
            self._drop_imap()
            raise

    def fetch_unseen(self) -> list[IncomingMessage]:
        """Return unread messages, leaving them unread."""
        messages: list[IncomingMessage] = []
        with self._imap() as client:
            client.select_folder(self.config.mailbox)
            uids = client.search(["UNSEEN"])
            if not uids:
                return messages

            # PEEK: the worker decides when something counts as read.
            response = client.fetch(uids, ["BODY.PEEK[]"])
            for uid, data in response.items():
                raw = data.get(b"BODY[]") or data.get(b"RFC822")
                if not raw:
                    log.warning("message %s came back with no body", uid)
                    continue
                parsed = email.message_from_bytes(raw, policy=email.policy.default)
                # "Pedro <a@b.com>" and a bare "a@b.com" both reduce to the
                # address, which is what the allow-list is written in terms of.
                sender = parseaddr(str(parsed.get("From") or ""))[1]
                messages.append(
                    IncomingMessage(
                        uid=int(uid),
                        sender=sender.strip().lower(),
                        subject=str(parsed.get("Subject") or ""),
                        body=_extract_body(parsed),
                        message_id=str(parsed.get("Message-ID") or "").strip(),
                    )
                )
        return messages

    def mark_seen(self, uid: int) -> None:
        with self._imap() as client:
            client.select_folder(self.config.mailbox)
            client.add_flags([uid], [SEEN])

    # ------------------------------------------------------------------ SMTP

    def _drop_smtp(self) -> None:
        server, self._smtp_conn = self._smtp_conn, None
        if server is None:
            return
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            log.debug("SMTP quit failed", exc_info=True)

    @contextmanager
    def _smtp(self) -> Iterator[smtplib.SMTP]:
        if self._pooled:
            if self._smtp_conn is None:
                self._smtp_conn = self._connect_smtp()
            try:
                yield self._smtp_conn
            except Exception:
                self._drop_smtp()
                raise
            return

        server = self._connect_smtp()
        try:
            yield server
        finally:
            self._smtp_conn = server
            self._drop_smtp()

    def _connect_smtp(self) -> smtplib.SMTP:
        if self.config.smtp_use_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                self.config.smtp_host,
                self.config.smtp_port,
                timeout=NETWORK_TIMEOUT_SECONDS,
                context=self.ssl_context,
            )
        else:
            server = smtplib.SMTP(
                self.config.smtp_host,
                self.config.smtp_port,
                timeout=NETWORK_TIMEOUT_SECONDS,
            )
        server.ehlo()
        if not self.config.smtp_use_ssl:
            self._upgrade(server)
        server.login(self.config.email_user, self.config.email_password)
        return server

    def _upgrade(self, server: smtplib.SMTP) -> None:
        """Encrypt the session before the password goes over it.

        A relay on the same machine may legitimately offer no STARTTLS, which
        is why the opt-out exists -- but it has to be asked for, rather than
        being what happens by default when a server simply does not offer
        encryption.
        """
        if server.has_extn("starttls"):
            server.starttls(context=self.ssl_context)
            server.ehlo()
            return

        if self.config.smtp_allow_cleartext:
            log.warning(
                "%s:%s does not offer STARTTLS; sending unencrypted because "
                "SMTP_ALLOW_CLEARTEXT is set",
                self.config.smtp_host,
                self.config.smtp_port,
            )
            return

        raise smtplib.SMTPNotSupportedError(
            f"{self.config.smtp_host}:{self.config.smtp_port} does not offer "
            "STARTTLS, so the password would be sent in the clear. Use port "
            "465 for implicit TLS, or set SMTP_ALLOW_CLEARTEXT=true if this is "
            "a trusted relay on your own network."
        )

    def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        attachment: Path | None = None,
        in_reply_to: str = "",
    ) -> None:
        """Send one message, optionally carrying an EPUB.

        ``in_reply_to`` threads the message onto the one that prompted it.
        A reply to a conversation the recipient started reads as wanted mail;
        an unsolicited message from a young domain reads as spam, which is
        what a bare confirmation was being filed as.
        """
        message = EmailMessage()
        message["From"] = self.config.email_user
        message["To"] = to
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain=self.config.email_user.split("@")[-1])
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to
            # RFC 3834: says plainly that a machine wrote this, and stops
            # anything replying back to it.
            message["Auto-Submitted"] = "auto-replied"
        message.set_content(body)

        if attachment is not None:
            data = attachment.read_bytes()
            message.add_attachment(
                data,
                maintype=EPUB_MEDIA_TYPE[0],
                subtype=EPUB_MEDIA_TYPE[1],
                filename=attachment.name,
            )

        if self.config.dry_run:
            log.info(
                "dry run: would send %r to %s%s",
                subject,
                to,
                f" with {attachment.name}" if attachment else "",
            )
            return

        with self._smtp() as server:
            server.send_message(message)
