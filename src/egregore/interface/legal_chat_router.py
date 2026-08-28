"""Legal chat router for bootstrap app.

Reuses chat logic from anchorum_http so the same endpoint is available
on the main Projection Plane (port 8443) without duplicating code.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from egregore.interface.anchorum_http import (
    CHAT_MODEL,
    EMS_URL,
    ChatIn,
    _ASK_SYSTEM,
    _EVIDENCE_INSTRUCTION,
    _LEGAL_SYSTEM,
    _MAX_CONTEXT_CHARS,
    _REFUSAL_RECOVERY_NOTE,
    _case_context,
    _chat_with_retries,
    _has_citations,
    _legal_grounding,
    _looks_like_refusal,
    _retrieve_case_chunks,
    _truncate_evidence,
)
import httpx
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/v1/anchorum/chat")
async def anchorum_chat(payload: ChatIn) -> Any:
    """Proxy chat to the Egregore Core API, mirroring anchorum_http."""
    messages: list[dict[str, str]] = []
    sources: list[dict[str, Any]] = []
    user_content = payload.message
    evidence_provided = False

    if payload.mode == "legal":
        system = _LEGAL_SYSTEM
        grounding = _legal_grounding(payload.message)
        if grounding:
            system += (
                "\n\nQuebec legal reference (verified excerpts; still flag "
                "anything uncertain as 'to verify on LégisQuébec'):\n" + grounding
            )
        if payload.case_id:
            system += "\n\nLive case data:\n" + _case_context(payload.case_id)
            from starlette.concurrency import run_in_threadpool

            retrieved, sources = await run_in_threadpool(
                _retrieve_case_chunks, payload.case_id, payload.message
            )
            if retrieved:
                evidence_provided = True
                system += "\n\n" + _EVIDENCE_INSTRUCTION
                evidence_budget = max(
                    2000,
                    _MAX_CONTEXT_CHARS
                    - len(system)
                    - len(payload.message)
                    - len(_EVIDENCE_INSTRUCTION)
                    - 200,
                )
                retrieved = _truncate_evidence(retrieved, evidence_budget)
                user_content = (
                    "CASE DOCUMENTS:\n"
                    f"{retrieved}\n\n"
                    f"QUESTION: {payload.message}\n\n"
                    "Answer using ONLY the case documents above, citing each "
                    "claim as [1], [2], etc. If they do not answer the "
                    "question, say exactly: 'The provided case documents do "
                    "not answer this question.'"
                )
            else:
                system += (
                    "\n\nNo relevant documents found in the case file for "
                    "this question. If the question is about the case, say "
                    "that explicitly. If it is a general legal question, "
                    "answer it from the Quebec legal reference and your "
                    "legal knowledge."
                )
        messages.append({"role": "system", "content": system})
    else:
        messages.append({"role": "system", "content": _ASK_SYSTEM})

    messages.append({"role": "user", "content": user_content})

    try:
        data = await _chat_with_retries(messages)
    except httpx.HTTPError as exc:
        logger.error("Core API chat failed after retries: %s", exc)
        return JSONResponse(
            status_code=502,
            content={"detail": f"Egregore core unreachable: {exc}"},
        )

    content = data.get("message", {}).get("content", "")
    refusal_retry = False
    citation_missing = False

    if (
        payload.mode == "legal"
        and evidence_provided
        and _looks_like_refusal(content)
    ):
        refusal_retry = True
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": _REFUSAL_RECOVERY_NOTE})
        try:
            data = await _chat_with_retries(messages)
            content = data.get("message", {}).get("content", "")
        except httpx.HTTPError as exc:
            logger.error("Refusal-recovery chat failed: %s", exc)

    if payload.mode == "legal" and evidence_provided and not _has_citations(content):
        citation_missing = True

    return {
        "ok": True,
        "content": content,
        "usage": data.get("usage", {}),
        "governance": data.get("governance", {}),
        "sources": sources,
        "model": CHAT_MODEL,
        "rag_telemetry": {
            "retrieved_chunks": len(sources),
            "distances": [s.get("distance") for s in sources],
            "refusal_retry": refusal_retry,
            "citation_missing": citation_missing,
        },
    }
