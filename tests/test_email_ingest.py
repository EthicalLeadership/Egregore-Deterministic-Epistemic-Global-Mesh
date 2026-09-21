"""Headless tests for email_ingest.py — no Tk, no network, no real IMAP.

A FakeIMAP connection object stands in for imaplib; _connect is monkeypatched.
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import email_ingest  # noqa: E402
from email_ingest import (  # noqa: E402
    ImapConfig,
    count_messages,
    derive_email_staging_dir,
    list_folders,
    parse_message_metadata,
    stage_email_fetch,
)
from file_fetch import ConsentLedger  # noqa: E402

SIMPLE = (
    b"From: alice@example.com\r\nTo: bob@example.com\r\n"
    b"Subject: Rendez-vous demain\r\nDate: Mon, 1 Jun 2026 10:00:00 -0400\r\n"
    b"Message-ID: <m1@example.com>\r\nContent-Type: text/plain; charset=utf-8\r\n"
    b"\r\nOn se voit a 14h.\r\n"
)

MULTIPART = (
    b"From: ciusss@ssss.gouv.qc.ca\r\nTo: bob@example.com\r\n"
    b"Subject: Confirmation\r\nContent-Type: multipart/mixed; boundary=BB\r\n"
    b"\r\n--BB\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
    b"Votre rendez-vous est confirme.\r\n"
    b"--BB\r\nContent-Type: application/pdf\r\n\r\n%PDF-fake\r\n--BB--\r\n"
)


class FakeIMAP:
    """Minimal IMAP4 stand-in: login/list/status/select/search/fetch/logout."""

    def __init__(self, folders: dict[str, list[bytes]], fail_login: bool = False):
        self._folders = folders
        self._fail_login = fail_login
        self._current: str | None = None

    def login(self, user: str, password: str) -> None:
        if self._fail_login:
            raise OSError("authentication failed")

    def list(self):
        lines = [f'(\\HasNoChildren) "/" "{name}"'.encode() for name in self._folders]
        return "OK", lines

    def status(self, folder: str, _what: str):
        name = folder.strip('"')
        if name not in self._folders:
            return "NO", [b"no such folder"]
        return "OK", [f'"{name}" (MESSAGES {len(self._folders[name])})'.encode()]

    def select(self, folder: str, readonly: bool = True):
        name = folder.strip('"')
        if name not in self._folders:
            return "NO", []
        self._current = name
        return "OK", [str(len(self._folders[name])).encode()]

    def search(self, _charset, _crit):
        uids = [str(i + 1).encode() for i in range(len(self._folders[self._current]))]
        return "OK", [b" ".join(uids)]

    def fetch(self, uid, _what: str):
        idx = int(uid) - 1
        raw = self._folders[self._current][idx]
        return "OK", [(b"1 (RFC822 {0}", raw)]

    def logout(self):
        return "BYE", []


def _config() -> ImapConfig:
    return ImapConfig(host="imap.example.com", user="bob@example.com", password="x")


@pytest.fixture
def fake_server(monkeypatch: pytest.MonkeyPatch):
    folders = {"INBOX": [SIMPLE, MULTIPART], "Archive": [SIMPLE]}
    fake = FakeIMAP(folders)
    monkeypatch.setattr(email_ingest, "_connect", lambda cfg: fake)
    return fake


def test_list_folders_ok(fake_server) -> None:
    ok, folders = list_folders(_config())
    assert ok is True
    assert folders == ["Archive", "INBOX"]


def test_list_folders_connection_refused() -> None:
    ok, err = list_folders(ImapConfig(host="127.0.0.1", port=1, user="u", password="p", use_ssl=False))
    assert ok is False
    assert "connect/login failed" in err


def test_count_messages(fake_server) -> None:
    ok, counts = count_messages(_config(), ["INBOX", "Archive", "Missing"])
    assert ok is True
    assert counts == {"INBOX": 2, "Archive": 1, "Missing": -1}


def test_parse_simple_message() -> None:
    meta = parse_message_metadata(SIMPLE)
    assert meta["subject"] == "Rendez-vous demain"
    assert meta["from"] == "alice@example.com"
    assert "14h" in meta["body_preview"]


def test_parse_multipart_extracts_text_plain() -> None:
    meta = parse_message_metadata(MULTIPART)
    assert meta["content_type"] == "multipart/mixed"
    assert "confirme" in meta["body_preview"]


def test_staging_dir_derivation() -> None:
    d = derive_email_staging_dir("CASE 1", "bob@imap.example.com", "20260809T000000Z", base=Path("/tmp/f"))
    assert d == Path("/tmp/f/CASE_1/email__bob_imap.example.com__20260809T000000Z")


def test_stage_email_fetch_manifest(tmp_path: Path, fake_server) -> None:
    staging = tmp_path / "case" / "email__x__ts"
    manifest = stage_email_fetch(
        _config(), ["INBOX", "Archive"], staging, "consenthash123", case="CASE-1"
    )
    assert manifest["message_count"] == 3
    assert manifest["errors"] == []
    assert manifest["consent_hash"] == "consenthash123"
    assert manifest["cancelled"] is False

    eml = staging / "INBOX" / "1.eml"
    assert eml.read_bytes() == SIMPLE
    assert manifest["messages"][0]["sha256"] == hashlib.sha256(SIMPLE).hexdigest()
    meta = json.loads((staging / "INBOX" / "2.json").read_text(encoding="utf-8"))
    assert meta["subject"] == "Confirmation"
    # Manifest on disk matches the returned manifest.
    on_disk = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk["message_count"] == 3


def test_stage_email_fetch_cancel(tmp_path: Path, fake_server) -> None:
    cancel = threading.Event()
    cancel.set()  # cancel before the first message
    manifest = stage_email_fetch(
        _config(), ["INBOX"], tmp_path / "s", "h", cancel=cancel
    )
    assert manifest["cancelled"] is True
    assert manifest["message_count"] == 0


def test_ledger_records_email_events(tmp_path: Path) -> None:
    ledger = ConsentLedger(tmp_path / "email_consent_ledger.jsonl")
    ledger.append("request", account="bob@imap.example.com", folders=["INBOX"], message_count=5)
    ledger.append("grant_once", account="bob@imap.example.com", folders=["INBOX"], message_count=5)
    intact, count, broken = ledger.verify()
    assert intact is True and count == 2 and broken is None
    # Tamper: flip the first entry's account.
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["account"] = "mallory@evil.example.com"
    lines[0] = json.dumps(first, sort_keys=True)
    ledger.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    intact, _, broken = ledger.verify()
    assert intact is False and broken == 1
