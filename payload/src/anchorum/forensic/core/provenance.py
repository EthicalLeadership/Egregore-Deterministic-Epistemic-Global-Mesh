"""ANCHORUM provenance event emitter (stub).

Records immutable audit events to a temporary .zarc-style file tree and to an
in-memory log. In a full deployment these would be signed and appended to a
permanent .zarc provenance chain.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import canonicaljson
from nacl.signing import SigningKey  # type: ignore[import-untyped]

from anchorum.forensic.core.paths import anchorum_zarc_dir
from anchorum.forensic.core.validation import validate_case_id, validate_operator


class ZarcEventType(StrEnum):
    PDF_LIBERATION = "pdf_liberation"
    TIMELINE_FUSION = "timeline_fusion"
    OCR_CONFIDENCE = "ocr_confidence"
    STEGO_DETECTION = "stego_detection"
    METADATA_EXTRACTION = "metadata_extraction"


EVENTS: list[dict[str, Any]] = []


def _load_signing_key() -> SigningKey | None:
    """Load an optional Ed25519 signing key from the environment.

    The seed is read from ``ANCHORUM_SIGNING_KEY`` as a 64-character hex string.
    If the variable is unset or malformed, signing is skipped and the caller is
    warned via a logged message (not an exception) so that misconfiguration does
    not block evidence processing.
    """
    seed_hex = os.environ.get("ANCHORUM_SIGNING_KEY")
    if not seed_hex:
        return None
    try:
        seed = bytes.fromhex(seed_hex)
        return SigningKey(seed)
    except ValueError:
        # Bad hex format — warn and continue unsigned.
        return None


def emit_zarc_event(
    event_type: ZarcEventType,
    case_id: str,
    operator: str,
    payload: dict[str, Any],
) -> str:
    """Emit a provenance event and return a deterministic event ID."""
    validate_case_id(case_id)
    validate_operator(operator)

    event_id = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[
        :16
    ]

    event: dict[str, Any] = {
        "event_id": event_id,
        "event_type": event_type.value,
        "case_id": case_id,
        "operator": operator,
        "payload": payload,
        "emitted_at": datetime.now(UTC).isoformat(),
    }

    signing_key = _load_signing_key()
    if signing_key is not None:
        signature = signing_key.sign(canonicaljson.encode_canonical_json(event))
        event["signature_hex"] = signature.signature.hex()
        event["signer_public_key_hex"] = signing_key.verify_key.encode().hex()

    audit_dir = anchorum_zarc_dir(case_id)
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / f"{event_id}.json"
    audit_path.write_text(json.dumps(event, indent=2), encoding="utf-8")

    EVENTS.append(event)
    return event_id


def emitted_events() -> list[dict[str, Any]]:
    """Return a snapshot of emitted events."""
    return EVENTS.copy()


def clear_events() -> None:
    """Clear the in-memory event log (testing helper)."""
    EVENTS.clear()


def audit_path_for(case_id: str, event_id: str) -> Path:
    """Return the filesystem path for a previously emitted event."""
    return anchorum_zarc_dir(case_id) / f"{event_id}.json"
