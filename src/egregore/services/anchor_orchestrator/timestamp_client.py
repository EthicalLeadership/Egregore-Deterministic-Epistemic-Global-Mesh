"""Timestamp Authority client for public anchoring with local fallback.

This module is the version used by the anchor-orchestrator composition root.
It returns ``TimestampToken`` objects that expose both the internal shape
(``cms_bytes``, ``timestamp_iso``, ``tier``) and a test-compatible response
surface (``source``, ``verified``, ``token``, ``timestamp_ns``).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol


class TimestampError(Exception):
    pass


class TsaForgeryError(TimestampError):
    """A granted TSA token failed cryptographic verification.

    This is never eligible for local fallback: silently downgrading a
    suspected forgery would mask the attack. Callers (anchor orchestrator)
    route this to the freeze controller.
    """


@dataclass
class TimestampToken:
    cms_bytes: bytes
    timestamp_iso: str
    tier: int
    verification: object | None = None  # TsaVerificationReport for tier-2

    @property
    def source(self) -> str:
        if self.tier >= 2:
            return "rfc3161"
        if self.tier == 1:
            return "local"
        return "mock"

    @property
    def verified(self) -> bool:
        """True only when a trusted-authority verification report passed.

        Mock and local tier-1 tokens are self-asserted: they are checkable
        but not independently verified, so they report False.
        """
        if self.verification is None:
            return False
        return bool(getattr(self.verification, "verdict", False))

    @property
    def token(self) -> str:
        return base64.b64encode(self.cms_bytes).decode("utf-8")

    @property
    def timestamp_ns(self) -> int:
        return int(datetime.fromisoformat(self.timestamp_iso).timestamp() * 1e9)


class ITimestampClient(Protocol):
    def timestamp(self, data_hash: str) -> TimestampToken: ...


class MockTimestampClient(ITimestampClient):
    def timestamp(self, data_hash: str) -> TimestampToken:
        return TimestampToken(b"mock", datetime.now(UTC).isoformat(), 0)


class LocalFallbackTimestampClient(ITimestampClient):
    """Creates a self-signed Tier-1 timestamp token when the TSA is unavailable.

    If a signing key is provided, the token is an Ed25519-signed object:
        base64(str({"hash": ..., "ts": ..., "sig": ..., "pk": ..., "source": "local"}))
    Otherwise a deterministic SHA-256 fallback token is returned.
    """

    def __init__(self, signing_key: str | None = None) -> None:
        self._signing_key = signing_key

    def timestamp(self, data_hash: str) -> TimestampToken:
        from egregore.kernel.ed25519_signer import (
            get_verify_key_hex,
            sign_message,
        )

        timestamp_ns = int(datetime.now(UTC).timestamp() * 1e9)
        ts = datetime.fromtimestamp(timestamp_ns / 1e9, tz=UTC).isoformat()

        if self._signing_key:
            payload = f"LOCALTS|{data_hash}|{timestamp_ns}"
            signature = sign_message(
                signing_key_hex=self._signing_key,
                message=payload.encode("utf-8"),
            )
            token_obj = {
                "hash": data_hash,
                "ts": timestamp_ns,
                "sig": signature,
                "pk": get_verify_key_hex(self._signing_key),
                "source": "local",
            }
            cms_bytes = str(token_obj).encode("utf-8")
        else:
            sig = hashlib.sha256(f"{data_hash}:{ts}".encode()).hexdigest()
            cms_bytes = sig.encode()

        return TimestampToken(cms_bytes=cms_bytes, timestamp_iso=ts, tier=1)


class RFC3161TimestampClient(ITimestampClient):
    """RFC 3161 timestamp client with optional local fallback.

    Every granted token is cryptographically verified (messageImprint, nonce,
    CMS signature, pinned trust chain, time-stamping EKU) before use.
    Network/parse failures may fall back to the local tier; verification
    failures raise :class:`TsaForgeryError` and never fall back.
    """

    def __init__(
        self,
        tsa_url: str = "https://freetsa.org/tsr",
        fallback: ITimestampClient | None = None,
        trust_dir: str | Path | None = None,
    ):
        self.tsa_url = tsa_url
        self.fallback = fallback
        self.trust_dir = Path(trust_dir) if trust_dir is not None else None

    def timestamp(self, data_hash: str) -> TimestampToken:
        try:
            return self._call_tsa(data_hash)
        except TsaForgeryError:
            raise
        except Exception as exc:
            if self.fallback is None:
                raise TimestampError(
                    f"TSA failed and no fallback configured: {exc}"
                ) from exc
            return self.fallback.timestamp(data_hash)

    def _call_tsa(self, data_hash: str) -> TimestampToken:
        import requests
        from asn1crypto import algos, core, tsp

        from egregore.infrastructure.tsa_verifier import verify_tsa_token

        hash_bytes = bytes.fromhex(data_hash)
        nonce = int.from_bytes(secrets.token_bytes(8), "big")
        req = tsp.TimeStampReq(
            {
                "version": 1,
                "message_imprint": {
                    "hash_algorithm": algos.DigestAlgorithm(
                        {"algorithm": "2.16.840.1.101.3.4.2.1"}
                    ),
                    "hashed_message": core.OctetString(hash_bytes),
                },
                "cert_req": True,
                "nonce": nonce,
            }
        )

        resp = requests.post(
            self.tsa_url,
            data=req.dump(),
            headers={"Content-Type": "application/timestamp-query"},
            timeout=10,
        )
        resp.raise_for_status()

        ts_resp = tsp.TimeStampResp.load(resp.content)
        status = ts_resp["status"]["status"].native

        # .native converts 0 -> "granted", 1 -> "grantedWithMods", 2 -> "rejection"
        if status not in (0, "granted", "grantedWithMods"):
            raise TimestampError(f"TSA rejected: status={status}")

        token = ts_resp["time_stamp_token"]
        if token is None:
            raise TimestampError("TSA response missing time_stamp_token")

        report = verify_tsa_token(
            token_bytes=token.dump(),
            expected_hash_hex=data_hash,
            nonce=nonce,
            trust_dir=self.trust_dir,
        )
        if not report.verdict:
            raise TsaForgeryError(
                "TSA token failed verification: " + "; ".join(report.failures)
            )
        if report.gen_time_iso is None:
            raise TsaForgeryError("TSA token missing gen_time after verification")

        return TimestampToken(
            cms_bytes=token.dump(),
            timestamp_iso=report.gen_time_iso,
            tier=2,
            verification=report,
        )
