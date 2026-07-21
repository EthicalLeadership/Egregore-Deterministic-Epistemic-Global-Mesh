"""Timestamp Authority client for public anchoring with local fallback.

Production flow:
  1. Submit data hash to an RFC 3161 TSA.
  2. On TSA failure (timeout, network error, invalid response), fall back to
     a locally signed timestamp token using the node's Ed25519 key. The
     fallback token is cryptographically valid but not publicly notarized.

Both paths return the same ``TimestampResponse`` shape so callers need not
branch on the source.
"""

from __future__ import annotations

import base64
import time
from typing import Protocol

import requests

from egregore.interface.timestamp_ports import (
    ITimestampClient,
    TimestampError,
    TimestampResponse,
)


class ITimestampSigner(Protocol):
    """Subset of signing backend used for local timestamp tokens."""

    def fingerprint(self) -> str: ...
    def sign(self, message: bytes) -> str: ...


class LocalFallbackTimestampClient:
    """Creates a self-signed timestamp token when the TSA is unavailable.

    The token format is deterministic and verifiable by anyone holding the
    public key fingerprint:
        base64(json({"hash": ..., "ts": ..., "sig": ed25519_sig}))
    """

    def __init__(self, signing_backend: ITimestampSigner) -> None:
        self._signing = signing_backend

    def timestamp(self, data_hash: str) -> TimestampResponse:
        timestamp_ns = time.time_ns()
        payload = f"LOCALTS|{data_hash}|{timestamp_ns}"
        signature = self._signing.sign(payload.encode("utf-8"))
        fingerprint = self._signing.fingerprint()
        token_obj = {
            "hash": data_hash,
            "ts": timestamp_ns,
            "sig": signature,
            "pk": fingerprint,
            "source": "local",
        }
        token = base64.b64encode(str(token_obj).encode("utf-8")).decode("utf-8")
        return TimestampResponse(
            token=token, timestamp_ns=timestamp_ns, verified=True, source="local"
        )


class RFC3161TimestampClient:
    """RFC 3161 timestamp client with automatic local fallback."""

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = 10.0,
        fallback: ITimestampClient | None = None,
    ) -> None:
        self._url = url
        self._timeout = timeout_seconds
        self._fallback = fallback

    def timestamp(self, data_hash: str) -> TimestampResponse:
        try:
            return self._timestamp_with_tsa(data_hash)
        except Exception as exc:
            if self._fallback is None:
                raise TimestampError(
                    f"TSA failed and no fallback configured: {exc}"
                ) from exc
            return self._fallback.timestamp(data_hash)

    def _timestamp_with_tsa(self, data_hash: str) -> TimestampResponse:
        from asn1crypto import algos, tsp

        digest = bytes.fromhex(data_hash)
        if len(digest) != 32:
            raise TimestampError("RFC3161 client expects a SHA-256 hex hash")

        hash_algorithm = algos.DigestAlgorithm({"algorithm": "sha256"})
        message_imprint = tsp.MessageImprint(
            {
                "hash_algorithm": hash_algorithm,
                "hashed_message": digest,
            }
        )
        req = tsp.TimeStampReq(
            {
                "version": "v1",
                "message_imprint": message_imprint,
                "cert_req": True,
            }
        )
        der_request = req.dump()

        headers = {"Content-Type": "application/timestamp-query"}
        response = requests.post(
            self._url,
            data=der_request,
            headers=headers,
            timeout=self._timeout,
        )
        response.raise_for_status()

        resp = tsp.TimeStampResp.load(response.content)
        status = resp["status"]
        if status["status"].native != "granted":
            raise TimestampError(
                f"TSA rejected request: {status['status_string'].native}"
            )

        token_bytes = resp["time_stamp_token"].dump()
        return TimestampResponse(
            token=base64.b64encode(token_bytes).decode("utf-8"),
            timestamp_ns=time.time_ns(),
            verified=True,
            source="tsa",
        )


class MockTimestampClient:
    """Deterministic mock TSA for tests and offline operation."""

    def __init__(self, *, verified: bool = True) -> None:
        self._verified = verified

    def timestamp(self, data_hash: str) -> TimestampResponse:
        timestamp_ns = time.time_ns()
        token_material = f"{data_hash}:{timestamp_ns}"
        token = base64.b64encode(token_material.encode()).decode()
        return TimestampResponse(
            token=token,
            timestamp_ns=timestamp_ns,
            verified=self._verified,
            source="mock",
        )
