"""
Federation HTTP surface for cross-node treaty and entropy exchange.

This router exposes the minimal endpoints needed for two Egregore nodes to
propose/ratify a federation treaty and exchange entropy signals over plain
HTTP (intended to run behind mTLS in production).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException

from egregore.application.entropy_exchange import EntropyExchange
from egregore.application.escalation_service import EscalationService
from egregore.application.federation_treaty import (
    InMemoryTreatyStore,
    RedisTreatyStore,
    TreatyLedger,
)
from egregore.domain.federation_constitution import (
    Constitution,
    EntropySignal,
    Treaty,
    load_constitution_from_source,
)
from egregore.infrastructure.file_system_domain_adapters import (
    FileSystemConstitutionAdapter,
)
from egregore.shared.freeze_state import FreezeController

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/federation", tags=["federation"])

# Module-level singletons so all requests share the same treaty ledger and
# entropy exchange state.
_constitution: Constitution | None = None
_ledger: TreatyLedger | None = None
_entropy: EntropyExchange | None = None


def _get_constitution() -> Constitution:
    global _constitution
    if _constitution is None:
        cfg_path = os.environ.get("EGREGORE_CONSTITUTION_PATH")
        if cfg_path is None:
            raise RuntimeError("EGREGORE_CONSTITUTION_PATH is not set")
        _constitution = load_constitution_from_source(
            FileSystemConstitutionAdapter(cfg_path)
        )
    return _constitution


def _make_treaty_store() -> RedisTreatyStore | InMemoryTreatyStore:
    try:
        from egregore.infrastructure.redis_store import redis_client_from_env

        client = redis_client_from_env(decode_responses=True)
        client.ping()
        return RedisTreatyStore(client)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Redis treaty store unavailable (%s); falling back to in-memory store.",
            exc,
        )
        return InMemoryTreatyStore()


def _get_ledger() -> TreatyLedger:
    global _ledger
    if _ledger is None:
        _ledger = TreatyLedger(
            constitution=_get_constitution(),
            store=_make_treaty_store(),
            provenance_sink=None,
        )
    return _ledger


def _get_entropy() -> EntropyExchange:
    global _entropy
    if _entropy is None:
        node_id = os.environ.get("EGREGORE_NODE_ID", "pioneer1")
        escalation = EscalationService(freeze_controller=FreezeController())
        _entropy = EntropyExchange(
            node_id=node_id,
            constitution=_get_constitution(),
            escalation_service=escalation,
        )
    return _entropy


def _treaty_to_dict(treaty: Treaty | None) -> dict[str, Any] | None:
    if treaty is None:
        return None
    data = asdict(treaty)
    data["state"] = treaty.state.value
    return data


@router.post("/treaty/propose")
async def treaty_propose(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Propose a new federation treaty."""
    try:
        treaty_id = payload.get("treaty_id") or f"treaty-{time.time_ns()}"
        treaty = _get_ledger().propose(
            treaty_id=treaty_id,
            parties=list(payload.get("parties", [])),
            clauses=list(payload.get("clauses", [])),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _treaty_to_dict(treaty)


@router.post("/treaty/ratify")
async def treaty_ratify(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Ratify a treaty on behalf of this node."""
    try:
        treaty = _get_ledger().ratify(
            treaty_id=payload["treaty_id"],
            node_id=payload["node_id"],
            signature=str(payload.get("signature", "")),
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"missing field: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _treaty_to_dict(treaty)


@router.get("/treaty/active")
async def treaty_active() -> dict[str, Any] | None:
    """Return the currently active treaty, if any."""
    return _treaty_to_dict(_get_ledger().active_treaty())


@router.post("/entropy")
async def entropy_receive(payload: dict[str, Any]) -> dict[str, Any]:
    """Receive an entropy signal from a peer node."""
    try:
        signal = EntropySignal(**payload)
    except TypeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    exchange = _get_entropy()
    exchange.receive(signal)
    return {
        "aggregate": exchange.aggregate(),
        "signals": [asdict(s) for s in exchange.latest_signals()],
    }


@router.get("/entropy")
async def entropy_get() -> dict[str, Any]:
    """Return the latest entropy aggregate and unexpired signals."""
    exchange = _get_entropy()
    return {
        "aggregate": exchange.aggregate(),
        "signals": [asdict(s) for s in exchange.latest_signals()],
    }
