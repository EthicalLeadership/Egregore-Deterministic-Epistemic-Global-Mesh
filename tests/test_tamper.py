"""Tamper-evidence tests for the signed provenance chain."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from egregore.kernel.provenance import Provenance


@pytest.fixture
def signing_key() -> str:
    from nacl.signing import SigningKey

    return SigningKey.generate().encode().hex()


def test_provenance_detects_line_tampering(signing_key: str) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        zarc_path = Path(tmpdir) / "chain.zarc"
        provenance = Provenance(zarc_path, signing_key_hex=signing_key)
        provenance.append(engine="test", event="created", payload={"id": 1})

        # Verify the freshly written line is valid.
        lines = zarc_path.read_text().strip().splitlines()
        assert len(lines) == 1
        assert provenance.verify_line(lines[0]) is True

        # Tamper with the line and ensure verification fails.
        obj = json.loads(lines[0])
        obj["payload"]["id"] = 999
        tampered = json.dumps(obj, sort_keys=True, separators=(",", ":"))
        assert provenance.verify_line(tampered) is False


def test_provenance_detects_signature_tampering(signing_key: str) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        zarc_path = Path(tmpdir) / "chain.zarc"
        provenance = Provenance(zarc_path, signing_key_hex=signing_key)
        provenance.append(engine="test", event="created", payload={"id": 1})

        lines = zarc_path.read_text().strip().splitlines()
        obj = json.loads(lines[0])
        obj["sig"] = "0" * 128
        tampered = json.dumps(obj, sort_keys=True, separators=(",", ":"))
        assert provenance.verify_line(tampered) is False


def test_provenance_chain_verifies_intact_chain(signing_key: str) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        zarc_path = Path(tmpdir) / "chain.zarc"
        provenance = Provenance(zarc_path, signing_key_hex=signing_key)
        provenance.append(engine="test", event="e1", payload={"n": 1})
        provenance.append(engine="test", event="e2", payload={"n": 2})

        assert provenance.verify_chain() is True


def test_provenance_chain_fails_on_broken_hash_chain(signing_key: str) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        zarc_path = Path(tmpdir) / "chain.zarc"
        provenance = Provenance(zarc_path, signing_key_hex=signing_key)
        provenance.append(engine="test", event="e1", payload={"n": 1})
        provenance.append(engine="test", event="e2", payload={"n": 2})

        lines = zarc_path.read_text().strip().splitlines()
        obj = json.loads(lines[0])
        obj["payload"]["n"] = 99
        lines[0] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
        zarc_path.write_text("\n".join(lines) + "\n")

        # Re-open to pick up the mutated chain.
        reloaded = Provenance(zarc_path, signing_key_hex=signing_key)
        assert reloaded.verify_chain() is False
