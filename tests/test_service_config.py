from __future__ import annotations

import pytest

from arxiv2epub.service.config import ConfigError, load_config

BASE = {
    "EMAIL_USER": "arxiv2kindle@outlook.com",
    "EMAIL_PASSWORD": "secret",
    "EMAIL_IMAP_HOST": "outlook.office365.com",
    "EMAIL_IMAP_PORT": "993",
    "EMAIL_SMTP_HOST": "smtp-mail.outlook.com",
    "EMAIL_SMTP_PORT": "587",
    "KINDLE_EMAIL": "me@kindle.com",
    "ALLOWED_SENDERS": "me@example.com",
    "POLL_INTERVAL_MS": "30000",
}


@pytest.fixture
def env(monkeypatch):
    def apply(**overrides):
        for key in list(BASE) + ["CHROME_PATH", "OUTPUT_DIR", "CACHE_DIR", "DRY_RUN"]:
            monkeypatch.delenv(key, raising=False)
        for key, value in {**BASE, **overrides}.items():
            if value is not None:
                monkeypatch.setenv(key, value)
        return load_config()

    return apply


def test_the_inherited_env_file_is_enough(env) -> None:
    config = env()
    assert config.email_user == BASE["EMAIL_USER"]
    assert config.imap_port == 993
    assert config.smtp_port == 587
    assert config.kindle_email == "me@kindle.com"


def test_the_poll_interval_is_read_as_milliseconds(env) -> None:
    # The previous worker expressed this in ms; the same value must mean the
    # same thing here.
    assert env(POLL_INTERVAL_MS="30000").poll_interval_seconds == 30.0
    assert env(POLL_INTERVAL_MS="60000").poll_interval_seconds == 60.0


def test_a_punishing_poll_interval_is_refused(env) -> None:
    with pytest.raises(ConfigError, match="hammer"):
        env(POLL_INTERVAL_MS="500")


def test_chrome_path_is_accepted_and_ignored(env) -> None:
    # Carried by the old .env; this version renders from HTML, but the key
    # must not cause a failure.
    config = env(CHROME_PATH="/usr/bin/chromium-browser")
    assert config.ignored_keys == ("CHROME_PATH",)


def test_senders_are_a_case_insensitive_list(env) -> None:
    config = env(ALLOWED_SENDERS="Me@Example.com, Other@Example.com ")
    assert config.allowed_senders == ("me@example.com", "other@example.com")
    assert config.allows("ME@EXAMPLE.COM")
    assert config.allows(" other@example.com ")
    assert not config.allows("stranger@example.com")


@pytest.mark.parametrize("missing", sorted(BASE))
def test_every_required_setting_is_reported_by_name(env, missing) -> None:
    if missing in ("EMAIL_IMAP_PORT", "EMAIL_SMTP_PORT", "POLL_INTERVAL_MS"):
        pytest.skip(f"{missing} has a default")
    with pytest.raises(ConfigError, match=missing):
        env(**{missing: None})


def test_an_empty_sender_list_is_refused(env) -> None:
    with pytest.raises(ConfigError, match="ALLOWED_SENDERS"):
        env(ALLOWED_SENDERS=" , ")


def test_port_465_means_implicit_tls(env) -> None:
    assert env(EMAIL_SMTP_PORT="465").smtp_use_ssl
    assert not env(EMAIL_SMTP_PORT="587").smtp_use_ssl


def test_the_summary_never_contains_the_password(env) -> None:
    assert "secret" not in env().describe()
