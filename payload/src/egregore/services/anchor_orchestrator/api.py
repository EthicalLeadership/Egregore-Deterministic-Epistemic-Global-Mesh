# epistemic marker: provenance / auditability
"""FastAPI router for the anchor orchestrator public verification API.

Fail-closed contract: ``GET /anchor/{anchor_id}/verify`` never claims
success it cannot prove. Any missing record, unconfigured trust store,
self-signed token, or failed cryptographic check returns
``verified: false`` with an explicit reason. There is no path in this
module that returns ``verified: true`` without a passing
``TsaVerificationReport``.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from egregore.services.anchor_orchestrator.service import AnchorOrchestrator


def _derive_anchor_id(block_hash: str) -> str:
    return hashlib.sha256(f"anchor:{block_hash}".encode()).hexdigest()


def _verify_local_token(notarization_hex: str, block_hash: str) -> dict[str, Any]:
    """Check a tier-1 local self-signed token. Always verified=False
    (self-asserted), but reports whether the signature is consistent."""
    from egregore.kernel.ed25519_signer import verify_message

    try:
        token_obj = ast.literal_eval(bytes.fromhex(notarization_hex).decode("utf-8"))
        payload = f"LOCALTS|{block_hash}|{token_obj['ts']}"
        consistent = verify_message(
            verify_key_hex=token_obj["pk"],
            message=payload.encode("utf-8"),
            signature_hex=token_obj["sig"],
        )
        return {
            "verified": False,
            "locally_verifiable": consistent,
            "reason": (
                "local self-signed token; signature "
                + ("consistent" if consistent else "INCONSISTENT")
                + " — not independently verified by a trusted authority"
            ),
        }
    except Exception as exc:
        return {
            "verified": False,
            "locally_verifiable": False,
            "reason": f"local token unparseable: {exc}",
        }


def create_anchor_router(
    *,
    anchor_store: Any,
    orchestrator: AnchorOrchestrator | None = None,
    trust_dir: str | Path | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/anchor", tags=["anchor"])

    @router.get("/{anchor_id}/verify")
    def verify_anchor(anchor_id: str) -> dict[str, Any]:
        """Verify a public anchor by ID. Fail-closed on every path."""
        if not anchor_id or len(anchor_id) != 64:
            raise HTTPException(status_code=400, detail="Invalid anchor_id")

        record = anchor_store.get_by_id(anchor_id)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail={"verified": False, "reason": "unknown anchor_id"},
            )

        # The record must bind to its own block hash.
        if _derive_anchor_id(record.block_hash) != record.anchor_id:
            return {
                "anchor_id": anchor_id,
                "verified": False,
                "reason": "anchor record binding mismatch (store integrity failure)",
            }

        base: dict[str, Any] = {
            "anchor_id": anchor_id,
            "block_hash": record.block_hash,
            "tier": record.tier,
        }

        if record.tier == "2":
            if trust_dir is None:
                return {
                    **base,
                    "verified": False,
                    "reason": "TSA trust anchor not configured",
                }
            from egregore.infrastructure.tsa_verifier import verify_tsa_token

            try:
                report = verify_tsa_token(
                    token_bytes=bytes.fromhex(record.notarization),
                    expected_hash_hex=record.block_hash,
                    nonce=None,  # nonce is anti-replay at request time only
                    trust_dir=Path(trust_dir),
                )
            except Exception as exc:
                return {
                    **base,
                    "verified": False,
                    "reason": f"TSA token unparseable: {exc}",
                }
            result: dict[str, Any] = {
                **base,
                "verified": report.verdict,
                "tsa_report": report.to_canonical(),
            }
            if not report.verdict:
                result["reason"] = "; ".join(report.failures)
            return result

        if record.tier == "1":
            return {**base, **_verify_local_token(record.notarization, record.block_hash)}

        return {
            **base,
            "verified": False,
            "reason": f"unsupported or mock anchor tier {record.tier!r}",
        }

    @router.post("/trigger")
    def trigger_anchoring(tenant_id: str = "default") -> dict[str, int]:
        """Trigger anchoring of all unanchored blocks for a tenant."""
        if orchestrator is None:
            raise HTTPException(
                status_code=503, detail="Anchor orchestrator not configured"
            )
        count = 0
        for _ in orchestrator.anchor_unanchored_blocks(tenant_id):
            count += 1
        return {"anchored_count": count}

    return router
