"""Minimal IMAP4 client adapter for email intake.

Uses only the Python standard library so it remains lightweight and
infrastructure-bound. Domain logic must consume the fetched messages through
an application-layer port; this module performs no parsing beyond UTF-8
content decoding.
"""

from __future__ import annotations

import contextlib
import email
import imaplib
from collections.abc import Iterable
from dataclasses import dataclass


class IMAPError(Exception):
    pass


class IMAPAuthError(IMAPError):
    pass


class IMAPConnectionError(IMAPError):
    pass


@dataclass(frozen=True)
class IMAPMessage:
    uid: str
    subject: str
    sender: str
    body_text: str


class IMAPConnector:
    """Thin, testable IMAP4_SSL wrapper."""

    def __init__(
        self,
        host: str,
        port: int = 993,
        *,
        timeout: float = 30.0,
        _client_factory=imaplib.IMAP4_SSL,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._client_factory = _client_factory
        self._client: imaplib.IMAP4_SSL | None = None

    def connect(self) -> None:
        try:
            self._client = self._client_factory(
                self.host, self.port, timeout=self.timeout
            )
        except OSError as exc:
            raise IMAPConnectionError(
                f"Could not connect to {self.host}:{self.port}: {exc}"
            ) from exc

    def login(self, username: str, password: str) -> None:
        if self._client is None:
            raise IMAPError("Not connected")
        try:
            status, _ = self._client.login(username, password)
            if status != "OK":
                raise IMAPAuthError("IMAP login rejected")
        except imaplib.IMAP4.error as exc:
            raise IMAPAuthError(f"IMAP login failed: {exc}") from exc

    def list_mailboxes(self) -> list[str]:
        if self._client is None:
            raise IMAPError("Not connected")
        status, folders = self._client.list()
        if status != "OK" or folders is None:
            return []
        mailboxes: list[str] = []
        for folder in folders:
            if folder is None:
                continue
            # folder is bytes like b'(\\HasNoChildren) "/" "INBOX"'
            decoded = folder.decode("utf-8", errors="replace")
            # Take the last quoted segment as the mailbox name.
            if '"' in decoded:
                mailboxes.append(decoded.split('"')[-2])
            else:
                mailboxes.append(decoded.split()[-1])
        return mailboxes

    def select_mailbox(self, mailbox: str = "INBOX") -> None:
        if self._client is None:
            raise IMAPError("Not connected")
        status, _ = self._client.select(mailbox)
        if status != "OK":
            raise IMAPError(f"Could not select mailbox {mailbox}")

    def fetch_unseen(
        self, mailbox: str = "INBOX", limit: int = 100
    ) -> Iterable[IMAPMessage]:
        """Fetch unseen messages from ``mailbox`` as ``IMAPMessage`` records."""
        self.select_mailbox(mailbox)
        if self._client is None:
            raise IMAPError("Not connected")

        status, uids = self._client.uid("search", None, "(UNSEEN)")
        if status != "OK" or uids is None:
            return

        uid_list = uids[0].decode("utf-8", errors="replace").split()
        for uid in uid_list[:limit]:
            msg = self._fetch_uid(uid)
            if msg is not None:
                yield msg

    def _fetch_uid(self, uid: str) -> IMAPMessage | None:
        if self._client is None:
            return None
        status, data = self._client.uid("fetch", uid, "(RFC822)")
        if status != "OK" or data is None:
            return None
        for part in data:
            if not isinstance(part, tuple) or len(part) < 2:
                continue
            raw_message = part[1]
            if isinstance(raw_message, bytes):
                parsed = email.message_from_bytes(raw_message)
                subject = self._decode_header(parsed.get("Subject", ""))
                sender = self._decode_header(parsed.get("From", ""))
                body_text = self._extract_text(parsed)
                return IMAPMessage(
                    uid=uid, subject=subject, sender=sender, body_text=body_text
                )
        return None

    @staticmethod
    def _decode_header(value: str | None) -> str:
        if value is None:
            return ""
        decoded_parts = email.header.decode_header(value)
        result: list[str] = []
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                result.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                result.append(part)
        return "".join(result)

    @staticmethod
    def _extract_text(parsed: email.message.Message) -> str:
        if parsed.is_multipart():
            for part in parsed.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        return payload.decode("utf-8", errors="replace")
            return ""
        payload = parsed.get_payload(decode=True)
        if isinstance(payload, bytes):
            return payload.decode("utf-8", errors="replace")
        return str(payload or "")

    def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
            with contextlib.suppress(Exception):
                self._client.logout()
            self._client = None

    def __enter__(self) -> IMAPConnector:
        self.connect()
        return self

    def __exit__(self, *exc: tuple[object, ...]) -> None:
        self.close()
