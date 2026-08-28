from __future__ import annotations

import email.policy
from email.message import EmailMessage

from arxiv2epub.ids import find_in_text
from arxiv2epub.service.mailbox import _extract_body


def _roundtrip(message: EmailMessage) -> EmailMessage:
    raw = message.as_bytes()
    return email.message_from_bytes(raw, policy=email.policy.default)


def _plain(text: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = "Pedro <me@example.com>"
    message["Subject"] = "paper"
    message.set_content(text)
    return message


def test_a_plain_message_reads_back_whole() -> None:
    body = _extract_body(_roundtrip(_plain("https://arxiv.org/abs/1706.03762")))
    assert "arxiv.org/abs/1706.03762" in body


def test_a_phone_style_multipart_message_still_yields_the_link() -> None:
    # Mail from a phone arrives as multipart/alternative; the previous worker
    # reached for part "1" and would miss the link when the parts were ordered
    # differently.
    message = _plain("sent from my phone")
    message.add_alternative(
        '<html><body><a href="https://arxiv.org/abs/2501.12948">R1</a></body></html>',
        subtype="html",
    )
    body = _extract_body(_roundtrip(message))
    assert find_in_text(body) is not None
    assert find_in_text(body).bare == "2501.12948"


def test_an_html_only_message_is_readable() -> None:
    message = EmailMessage()
    message["From"] = "me@example.com"
    message.set_content(
        '<html><body>read <a href="https://arxiv.org/abs/2301.08243">this</a></body></html>',
        subtype="html",
    )
    body = _extract_body(_roundtrip(message))
    assert find_in_text(body).bare == "2301.08243"


def test_a_non_utf8_message_does_not_raise() -> None:
    message = EmailMessage()
    message["From"] = "me@example.com"
    message.set_content("café arXiv:1706.03762", charset="iso-8859-1")
    body = _extract_body(_roundtrip(message))
    assert "1706.03762" in body
    assert "café" in body
