"""Agent router for Anchorum Legal Dossier."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from egregore.application.agents.orchestrator import AgentOrchestrator

router = APIRouter()


class AgentRequest(BaseModel):
    message: str
    case_id: str | None = None


@router.post("/api/v1/anchorum/agent")
async def agent_chat(req: AgentRequest):
    orchestrator = AgentOrchestrator()
    # If case_id is provided, append context to the message
    message = req.message
    if req.case_id:
        message = f"Case {req.case_id}: {req.message}"
    response = orchestrator.run(message)
    return {"response": response}
