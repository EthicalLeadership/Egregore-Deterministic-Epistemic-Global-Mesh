"""Base agent contract for the Anchorum system.

All agents inherit from this class. It enforces the constitution:
- agents cannot self-certify
- agents cannot release production artifacts
- agents have capability-scoped tools (not implemented fully yet)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field

from egregore.constitution import ConstitutionalAgent, ConstitutionalMixin


class AgentContext(BaseModel):
    """Input context for an agent run."""
    model_config = ConfigDict(frozen=True)

    matter_id: str
    scope: Dict[str, Any] = Field(default_factory=dict)
    raw_inputs: Dict[str, Any] = Field(default_factory=dict)


class AgentResult(BaseModel):
    """Structured result from an agent run.

    Contains findings but does NOT certify or release anything.
    """
    model_config = ConfigDict(frozen=True)

    agent_id: str
    run_id: str
    findings: Dict[str, Any] = Field(default_factory=dict)
    raw_anchorum_outputs: Dict[str, Any] = Field(default_factory=dict)
    assurance_report: Optional[Dict[str, Any]] = None


class BaseAgent(ConstitutionalAgent):
    """Common contract for all Anchorum agents."""

    agent_id: str = "base"
    version: str = "0.1.0"

    def __init__(self, **kwargs: Any) -> None:
        self.allowed_tools = kwargs.get("allowed_tools", set())
        self.forbidden_tools = kwargs.get("forbidden_tools", set())
        super().__init__(**kwargs)

    def run(self, context: AgentContext) -> AgentResult:
        raise NotImplementedError
