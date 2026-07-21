"""Tests for egregore.infrastructure.imap_connector."""

from __future__ import annotations

import email.message
import imaplib

import pytest

from egregore.infrastructure.imap_connector import (
    IMAPAuthError,
    IMAPConnector,
    IMAPError,
    IMAPMessage,
)


class _FakeIMAP:
    """Stand-in for imaplib.IMAP4_SSL that records calls and returns canned data."""

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.logged_in = False
        self.closed = False

    def login(self, username: str, password: str):
        if password == "wrong":  # noqa: S105
            raise imaplib.IMAP4.error("authentication failed")
        self.logged_in = True
        return ("OK", b"login success")

    def list(self):
        return (
            "OK",
            [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren) "/" "Sent"',
            ],
        )

    def select(self, mailbox: str):
        if mailbox == "MISSING":
            return ("NO", b"no such mailbox")
        return ("OK", [b"1"])

    def uid(self, command: str, *args):
        if command == "search":
            return ("OK", [b"101 102 103"])
        if command == "fetch":
            uid = args[0]
            msg = email.message.EmailMessage()
            msg["Subject"] = f"Test subject {uid}"
            msg["From"] = "sender@example.com"
            msg.set_content(f"Body for {uid}")
            return ("OK", [(b"RFC822", msg.as_bytes())])
        return ("NO", [b"unknown command"])

    def close(self):
        self.closed = True

    def logout(self):
        pass


def test_connector_connects_with_config() -> None:
    conn = IMAPConnector(
        "imap.example.com", 993, timeout=10.0, _client_factory=_FakeIMAP
    )
    conn.connect()
    assert conn._client is not None
    assert conn._client.host == "imap.example.com"
    assert conn._client.port == 993
    assert conn._client.timeout == 10.0


def test_login_success() -> None:
    conn = IMAPConnector("imap.example.com", _client_factory=_FakeIMAP)
    conn.connect()
    conn.login("user", "pass")
    assert conn._client.logged_in is True


def test_login_failure_raises_auth_error() -> None:
    conn = IMAPConnector("imap.example.com", _client_factory=_FakeIMAP)
    conn.connect()
    with pytest.raises(IMAPAuthError):
        conn.login("user", "wrong")


def test_login_without_connect_raises() -> None:
    conn = IMAPConnector("imap.example.com", _client_factory=_FakeIMAP)
    with pytest.raises(IMAPError):
        conn.login("user", "pass")


def test_list_mailboxes_parses_folder_names() -> None:
    conn = IMAPConnector("imap.example.com", _client_factory=_FakeIMAP)
    conn.connect()
    mailboxes = conn.list_mailboxes()
    assert "INBOX" in mailboxes
    assert "Sent" in mailboxes


def test_select_missing_mailbox_raises() -> None:
    conn = IMAPConnector("imap.example.com", _client_factory=_FakeIMAP)
    conn.connect()
    with pytest.raises(IMAPError):
        conn.select_mailbox("MISSING")


def test_fetch_unseen_returns_messages() -> None:
    conn = IMAPConnector("imap.example.com", _client_factory=_FakeIMAP)
    conn.connect()
    messages = list(conn.fetch_unseen("INBOX", limit=2))
    assert len(messages) == 2
    for _idx, msg in enumerate(messages):
        assert isinstance(msg, IMAPMessage)
        assert f"Test subject {msg.uid}" == msg.subject
        assert msg.sender == "sender@example.com"
        assert f"Body for {msg.uid}" in msg.body_text


def test_close_sets_client_to_none() -> None:
    conn = IMAPConnector("imap.example.com", _client_factory=_FakeIMAP)
    conn.connect()
    conn.close()
    assert conn._client is None


def test_context_manager_closes_on_exit() -> None:
    with IMAPConnector("imap.example.com", _client_factory=_FakeIMAP) as conn:
        assert conn._client is not None
    assert conn._client is None
