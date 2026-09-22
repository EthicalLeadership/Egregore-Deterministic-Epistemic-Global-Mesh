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
    # Pass case_id as agent context so the orchestrator can call ANCHORUM
    # tools deterministically instead of relying on regex extraction alone.
    response = orchestrator.run(req.message, case_id=req.case_id)
    return {"response": response}
