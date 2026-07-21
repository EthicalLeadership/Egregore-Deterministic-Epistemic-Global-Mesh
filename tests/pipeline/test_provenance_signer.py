"""Tests for Ed25519 provenance signing/verification."""

from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from egregore.pipeline.provenance_signer import (
    generate_signing_key,
    load_private_key,
    load_public_key,
    sign_provenance,
    verify_provenance,
)


def test_generate_signing_key_writes_pem_pair(tmp_path: Path) -> None:
    generate_signing_key(tmp_path)
    assert (tmp_path / "signing_key.pem").exists()
    assert (tmp_path / "signing_key.pub").exists()


def test_sign_verify_round_trip() -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    record = {"module_id": "mod", "manifest": {"name": "mod"}}

    signed = sign_provenance(record, private_key, "test-signer")

    assert signed["record"] == record
    assert signed["provenance"]["signer_id"] == "test-signer"
    assert signed["provenance"]["algorithm"] == "ed25519+sha256"
    assert verify_provenance(signed, public_key) is True


def test_verify_fails_when_record_tampered() -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    signed = sign_provenance({"module_id": "mod"}, private_key, "s")
    signed["record"]["module_id"] = "tampered"
    assert verify_provenance(signed, public_key) is False


def test_verify_fails_with_wrong_public_key() -> None:
    private_key = Ed25519PrivateKey.generate()
    other_public_key = Ed25519PrivateKey.generate().public_key()
    signed = sign_provenance({"module_id": "mod"}, private_key, "s")
    assert verify_provenance(signed, other_public_key) is False


def test_load_keys_from_files(tmp_path: Path) -> None:
    generate_signing_key(tmp_path)
    private_key = load_private_key(tmp_path / "signing_key.pem")
    public_key = load_public_key(tmp_path / "signing_key.pub")

    signed = sign_provenance({"k": "v"}, private_key, "file-signer")
    assert verify_provenance(signed, public_key) is True
