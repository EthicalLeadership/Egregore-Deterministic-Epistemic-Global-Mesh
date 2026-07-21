"""Shared red-team harness fixtures and helpers."""

from __future__ import annotations

from typing import Any

import pytest

from egregore.kernel.ed25519_signer import sign_message
from egregore.rfe.config import load_rfe_config
from egregore.shared.canonical import canonical_dumps


@pytest.fixture(scope="session")
def rfe_config(verify_key: str) -> dict[str, Any]:
    config = load_rfe_config()
    config.setdefault("security", {})["verify_keys"] = {"default": verify_key}
    return config


def sign_stream(stream: dict[str, Any], signing_key: str) -> dict[str, Any]:
    """Sign a stream dict (excluding signature field) and return a new dict."""
    signed = dict(stream)
    payload = {k: v for k, v in signed.items() if k != "signature"}
    message = canonical_dumps(payload).encode("utf-8")
    signed["signature"] = sign_message(signing_key_hex=signing_key, message=message)
    return signed


def base_manifest(timestamp: str = "2026-06-29T00:00:00+00:00") -> dict[str, Any]:
    return {
        "case_id": "case_redteam_001",
        "timestamp": timestamp,
        "streams": [],
        "constraints": {
            "max_pages": 20,
            "required_sections": [
                "summary",
                "timeline",
                "obstruction_analysis",
                "conclusion",
            ],
            "output_format": "pdf-a-1b",
            "language": "en",
        },
    }


def make_stream(
    stream_id: str,
    source_tier: int,
    claim: str,
    timestamp: str,
    half_life_hours: float | None = None,
    stype: str = "testimony",
    confidence: float = 0.9,
) -> dict[str, Any]:
    decay = None
    if half_life_hours is not None:
        decay = {
            "method": "exponential",
            "half_life_hours": half_life_hours,
            "justification": "Red-team decay policy.",
        }
    return {
        "stream_id": stream_id,
        "type": stype,
        "source_tier": source_tier,
        "content": {
            "claim": claim,
            "subject": "liability",
            "text": f"{stream_id} statement.",
        },
        "confidence": confidence,
        "provenance_hash": f"hash_{stream_id}",
        "signature": None,
        "timestamp": timestamp,
        "decay": decay,
        "severity_impact": 0.8,
        "relevance_tags": ["liability"],
    }
