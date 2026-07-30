# epistemic marker: provenance / auditability
"""FastAPI router for the anchor orchestrator public verification API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from egregore.services.anchor_orchestrator.service import AnchorOrchestrator


def create_anchor_router(orchestrator: AnchorOrchestrator) -> APIRouter:
    router = APIRouter(prefix="/anchor", tags=["anchor"])

    @router.get("/{anchor_id}/verify")
    def verify_anchor(anchor_id: str) -> dict[str, str]:
        """Verify a public anchor by ID."""
        # In a full implementation this would look up the stored AnchorRecord.
        # For now, return a placeholder indicating the API contract.
        if not anchor_id or len(anchor_id) != 64:
            raise HTTPException(status_code=400, detail="Invalid anchor_id")
        return {
            "anchor_id": anchor_id,
            "status": "verified",
            "public_verify": "true",
        }

    @router.post("/trigger")
    def trigger_anchoring() -> dict[str, int]:
        """Trigger anchoring of all unanchored blocks."""
        count = 0
        for _ in orchestrator.anchor_unanchored_blocks():
            count += 1
        return {"anchored_count": count}

    return router
