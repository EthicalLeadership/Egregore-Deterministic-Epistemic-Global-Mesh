"""FastAPI router for /v1/code — Claude-powered code factory."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from egregore.application.code_factory import (
    CodeFactoryService,
    CodeTask,
)

router = APIRouter(prefix="/v1", tags=["code"])


class CodeTaskRequest(BaseModel):
    task_type: str = Field(
        default="generate", pattern="^(generate|review|refactor|explain|test)$"
    )
    prompt: str = Field(min_length=1)
    language: str = Field(default="python")
    context: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    model: str = Field(default="claude-3-5-sonnet-20241022")
    deterministic: bool = Field(default=True)


class CodeArtifactResponse(BaseModel):
    task_type: str
    language: str
    model: str
    content: str
    usage: dict[str, int]
    inference_id: str
    governance: dict[str, bool]


def _get_code_factory(request: Request) -> CodeFactoryService:
    """Resolve CodeFactoryService from app state."""
    factory: CodeFactoryService | None = getattr(
        request.app.state, "code_factory", None
    )
    if factory is None:
        raise HTTPException(status_code=503, detail="Code factory not configured")
    return factory


@router.post("/code")
def run_code_task(req: CodeTaskRequest, request: Request) -> CodeArtifactResponse:
    """Execute a code-generation, review, refactor, explain, or test task."""
    factory = _get_code_factory(request)

    health = factory.health()
    if not any(
        b.get("reachable")
        for b in health.get("inference_health", {}).get("backends", {}).values()
    ):
        raise HTTPException(
            status_code=503, detail="No LLM backend available for code factory"
        )

    task = CodeTask(
        task_type=req.task_type,
        prompt=req.prompt,
        language=req.language,
        context=req.context,
        constraints=req.constraints,
        model=req.model,
        deterministic=req.deterministic,
    )

    try:
        artifact = factory.execute(task)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return CodeArtifactResponse(
        task_type=artifact.task_type,
        language=artifact.language,
        model=artifact.model,
        content=artifact.content,
        usage=artifact.usage,
        inference_id=artifact.inference_id,
        governance=artifact.governance,
    )


@router.get("/code/health")
def code_factory_health(request: Request) -> dict[str, Any]:
    """Return code-factory health including backend status."""
    factory = _get_code_factory(request)
    return factory.health()
