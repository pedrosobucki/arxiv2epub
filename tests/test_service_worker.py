from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from arxiv2epub.metadata import PaperMetadata
from arxiv2epub.models import Book
from arxiv2epub.pipeline import Result
from arxiv2epub.service.config import ServiceConfig
from arxiv2epub.service.mailbox import IncomingMessage
from arxiv2epub.service.worker import Worker
from arxiv2epub.sources import NoHtmlAvailable
from conftest import make_source


@dataclass
class SentMail:
    to: str
    subject: str
    body: str
    attachment: Path | None


@dataclass
class FakeMailbox:
    """Stands in for IMAP and SMTP so the tests stay offline."""

    inbox: list[IncomingMessage] = field(default_factory=list)
    sent: list[SentMail] = field(default_factory=list)
    seen: list[int] = field(default_factory=list)
    fail_on_fetch: bool = False

    def fetch_unseen(self) -> list[IncomingMessage]:
        if self.fail_on_fetch:
            raise ConnectionError("imap is down")
        return list(self.inbox)

    def mark_seen(self, uid: int) -> None:
        self.seen.append(uid)

    def send(self, *, to, subject, body, attachment=None) -> None:
        self.sent.append(SentMail(to, subject, body, attachment))

    def to_kindle(self) -> list[SentMail]:
        return [mail for mail in self.sent if mail.attachment is not None]


@pytest.fixture
def config(tmp_path) -> ServiceConfig:
    return ServiceConfig(
        email_user="worker@example.com",
        email_password="secret",
        imap_host="imap",
        imap_port=993,
        smtp_host="smtp",
        smtp_port=587,
        kindle_email="me@kindle.com",
        allowed_senders=("me@example.com",),
        poll_interval_seconds=30.0,
        output_dir=tmp_path / "out",
    )


def message(body: str = "", *, sender: str = "me@example.com", subject: str = "") -> IncomingMessage:
    return IncomingMessage(uid=7, sender=sender, subject=subject, body=body)


def _epub(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
    return path


def converter(paper_metadata: PaperMetadata, *, size_bytes: int = 700_000, warnings=()):
    def convert(reference, output, options):
        output = Path(output)
        path = _epub(output / f"attention-is-all-you-need-{reference}.epub")
        path.write_bytes(b"x" * size_bytes)
        book = Book(metadata=paper_metadata, source=make_source("<p/>"))
        book.warnings = list(warnings)
        return Result(path=path, book=book, warnings=list(warnings))

    return convert


def _worker(config, mailbox, convert) -> Worker:
    return Worker(config, mailbox=mailbox, convert=convert)


# --------------------------------------------------------------- happy path


def test_a_link_becomes_an_epub_on_its_way_to_the_kindle(
    config, paper_metadata
) -> None:
    mailbox = FakeMailbox([message("please read https://arxiv.org/abs/1706.03762")])
    worker = _worker(config, mailbox, converter(paper_metadata))

    assert worker.poll()[0].action == "sent"

    delivered = mailbox.to_kindle()
    assert len(delivered) == 1
    assert delivered[0].to == "me@kindle.com"
    assert delivered[0].subject == paper_metadata.title
    assert delivered[0].attachment.suffix == ".epub"


def test_the_sender_is_told_what_was_sent(config, paper_metadata) -> None:
    mailbox = FakeMailbox([message("arXiv:1706.03762")])
    _worker(config, mailbox, converter(paper_metadata)).poll()

    confirmation = next(m for m in mailbox.sent if m.attachment is None)
    assert confirmation.to == "me@example.com"
    assert paper_metadata.title in confirmation.body
    assert "me@kindle.com" in confirmation.body


def test_conversion_warnings_reach_the_sender(config, paper_metadata) -> None:
    mailbox = FakeMailbox([message("arXiv:1706.03762")])
    convert = converter(paper_metadata, warnings=["figure 3 could not be downloaded"])
    _worker(config, mailbox, convert).poll()

    confirmation = next(m for m in mailbox.sent if m.attachment is None)
    assert "figure 3 could not be downloaded" in confirmation.body


def test_the_link_may_be_in_the_subject(config, paper_metadata) -> None:
    mailbox = FakeMailbox([message("", subject="https://arxiv.org/abs/1706.03762")])
    assert _worker(config, mailbox, converter(paper_metadata)).poll()[0].action == "sent"


# ------------------------------------------------------------------ refusals


def test_mail_from_a_stranger_is_left_untouched(config, paper_metadata) -> None:
    mailbox = FakeMailbox([message("arXiv:1706.03762", sender="stranger@example.com")])
    outcome = _worker(config, mailbox, converter(paper_metadata)).poll()[0]

    assert outcome.action == "ignored"
    assert mailbox.sent == []
    # Deliberately still unread: it is not this worker's mail to consume.
    assert mailbox.seen == []


def test_a_message_with_no_link_gets_an_explanation(config, paper_metadata) -> None:
    mailbox = FakeMailbox([message("hello, how are you?")])
    outcome = _worker(config, mailbox, converter(paper_metadata)).poll()[0]

    assert outcome.action == "no-reference"
    assert mailbox.to_kindle() == []
    assert "could not find" in mailbox.sent[0].body.lower()
    assert mailbox.seen == [7]


def test_the_same_paper_twice_is_recognised_as_a_repeat(config, paper_metadata) -> None:
    _epub(config.output_dir / "attention-is-all-you-need-1706.03762v7.epub")
    mailbox = FakeMailbox([message("arXiv:1706.03762")])

    outcome = _worker(config, mailbox, converter(paper_metadata)).poll()[0]

    assert outcome.action == "duplicate"
    assert mailbox.to_kindle() == []
    assert "already" in mailbox.sent[0].subject.lower()


def test_an_unconvertible_paper_is_explained_rather_than_retried(
    config, paper_metadata
) -> None:
    def refuse(reference, output, options):
        raise NoHtmlAvailable("no HTML rendering of hep-th/9711200 could be fetched")

    mailbox = FakeMailbox([message("hep-th/9711200")])
    outcome = _worker(config, mailbox, refuse).poll()[0]

    assert outcome.action == "unconvertible"
    assert "ar5iv" in mailbox.sent[0].body
    assert mailbox.seen == [7], "a permanent failure must not be retried forever"


def test_an_unexpected_conversion_error_is_reported_to_the_sender(
    config, paper_metadata
) -> None:
    def explode(reference, output, options):
        raise RuntimeError("disk full")

    mailbox = FakeMailbox([message("arXiv:1706.03762")])
    outcome = _worker(config, mailbox, explode).poll()[0]

    assert outcome.action == "failed"
    assert "disk full" in mailbox.sent[0].body
    assert mailbox.to_kindle() == []


def test_an_oversized_book_is_not_mailed(config, paper_metadata) -> None:
    convert = converter(paper_metadata, size_bytes=30 * 1024 * 1024)
    mailbox = FakeMailbox([message("arXiv:1706.03762")])

    outcome = _worker(config, mailbox, convert).poll()[0]

    assert outcome.action == "too-large"
    assert mailbox.to_kindle() == []
    assert "30.0 MB" in mailbox.sent[0].body


# --------------------------------------------------------------- resilience


def test_an_unreachable_inbox_does_not_stop_the_worker(config, paper_metadata) -> None:
    mailbox = FakeMailbox(fail_on_fetch=True)
    assert _worker(config, mailbox, converter(paper_metadata)).poll() == []


def test_one_bad_message_does_not_block_the_next(config, paper_metadata) -> None:
    inbox = [
        IncomingMessage(uid=1, sender="me@example.com", subject="", body="nothing here"),
        IncomingMessage(uid=2, sender="me@example.com", subject="", body="arXiv:1706.03762"),
    ]
    mailbox = FakeMailbox(inbox)

    actions = [o.action for o in _worker(config, mailbox, converter(paper_metadata)).poll()]

    assert actions == ["no-reference", "sent"]
    assert mailbox.seen == [1, 2]


def test_a_failure_to_reply_does_not_lose_the_delivery(config, paper_metadata) -> None:
    class BrokenReply(FakeMailbox):
        def send(self, *, to, subject, body, attachment=None):
            if attachment is None:
                raise ConnectionError("smtp refused the confirmation")
            super().send(to=to, subject=subject, body=body, attachment=attachment)

    mailbox = BrokenReply([message("arXiv:1706.03762")])
    outcome = _worker(config, mailbox, converter(paper_metadata)).poll()[0]

    assert outcome.action == "sent"
    assert len(mailbox.to_kindle()) == 1


def test_a_stranger_is_only_logged_once(config, paper_metadata, caplog) -> None:
    # The message stays unread by design, so it returns on every pass; a
    # service running for months must not log it every time.
    mailbox = FakeMailbox([message("arXiv:1706.03762", sender="stranger@example.com")])
    worker = _worker(config, mailbox, converter(paper_metadata))

    with caplog.at_level("WARNING"):
        worker.poll()
        worker.poll()
        worker.poll()

    assert sum("ignoring message" in r.message for r in caplog.records) == 1
    assert mailbox.seen == []


def test_a_whole_pass_costs_one_login(config, paper_metadata, monkeypatch) -> None:
    """Providers throttle accounts that reconnect per message; a pass is one
    session, however much mail it finds."""
    from arxiv2epub.service.mailbox import Mailbox

    imap_logins = 0

    class FakeIMAP:
        def select_folder(self, name): pass
        def search(self, criteria): return [11, 12]
        def fetch(self, uids, parts):
            body = (
                b"From: me@example.com\r\nSubject: p\r\n\r\n"
                b"https://arxiv.org/abs/1706.03762\r\n"
            )
            return {uid: {b"BODY[]": body} for uid in uids}
        def add_flags(self, uids, flags): pass
        def logout(self): pass

    mailbox = Mailbox(config)

    def connect():
        nonlocal imap_logins
        imap_logins += 1
        return FakeIMAP()

    monkeypatch.setattr(mailbox, "_connect_imap", connect)
    monkeypatch.setattr(mailbox, "_connect_smtp", lambda: pytest.fail("unused"))
    monkeypatch.setattr(mailbox, "send", lambda **kw: None)

    Worker(config, mailbox=mailbox, convert=converter(paper_metadata)).poll()

    # Without pooling this would be 1 fetch + 2 mark_seen = 3.
    assert imap_logins == 1


def test_outside_a_session_each_call_still_stands_alone(config) -> None:
    from arxiv2epub.service.mailbox import Mailbox

    mailbox = Mailbox(config)
    assert mailbox._imap_conn is None
    with mailbox.session():
        assert mailbox._pooled
    # The session must not leak a connection or leave pooling switched on.
    assert not mailbox._pooled
    assert mailbox._imap_conn is None
