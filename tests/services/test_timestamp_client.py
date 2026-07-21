"""Tests for the timestamp client and local fallback."""

from __future__ import annotations

import pytest

from egregore.kernel.ed25519_signer import generate_signing_key, verify_message
from egregore.services.anchor_orchestrator.timestamp_client import (
    LocalFallbackTimestampClient,
    MockTimestampClient,
    RFC3161TimestampClient,
    TimestampError,
)


def test_mock_timestamp_client() -> None:
    client = MockTimestampClient()
    resp = client.timestamp("a" * 64)
    assert resp.source == "mock"
    assert resp.verified is True
    assert len(resp.token) > 0


def test_local_fallback_signature_is_verifiable() -> None:
    key = generate_signing_key()
    client = LocalFallbackTimestampClient(key)
    data_hash = "a" * 64
    resp = client.timestamp(data_hash)
    assert resp.source == "local"
    assert resp.verified is True

    import ast
    import base64

    token_obj = ast.literal_eval(base64.b64decode(resp.token).decode("utf-8"))
    payload = f"LOCALTS|{data_hash}|{resp.timestamp_ns}"
    assert (
        verify_message(
            verify_key_hex=token_obj["pk"],
            message=payload.encode("utf-8"),
            signature_hex=token_obj["sig"],
        )
        is True
    )


def test_rfc3161_client_falls_back_on_bad_url() -> None:
    key = generate_signing_key()
    fallback = LocalFallbackTimestampClient(key)
    client = RFC3161TimestampClient("http://127.0.0.1:1/tsa", fallback=fallback)
    resp = client.timestamp("a" * 64)
    assert resp.source == "local"


def test_rfc3161_client_raises_without_fallback() -> None:
    client = RFC3161TimestampClient("http://127.0.0.1:1/tsa")
    with pytest.raises(TimestampError):
        client.timestamp("a" * 64)
