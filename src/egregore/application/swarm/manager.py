"""Swarm Manager.

Executes multiple agents concurrently, isolates failures, and merges
structured results. Agents must not share mutable state; each runs in
its own context and returns an AgentResult.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from egregore.application.agents.base import BaseAgent, AgentContext, AgentResult


class SwarmError(Exception):
    """Raised when the swarm cannot run at all."""


@dataclass
class SwarmResult:
    """Aggregated results from all agents."""
    results: List[AgentResult] = field(default_factory=list)
    errors: Dict[str, str] = field(default_factory=dict)

    def get(self, agent_id: str) -> Optional[AgentResult]:
        for r in self.results:
            if r.agent_id == agent_id:
                return r
        return None

    def has_errors(self) -> bool:
        return bool(self.errors)


class SwarmManager:
    """Runs multiple agents in parallel, capturing successes and failures."""

    def __init__(self, agents: List[BaseAgent], max_workers: int = 4):
        self.agents = agents
        self.max_workers = max_workers

    def run(self, contexts: Dict[str, AgentContext]) -> SwarmResult:
        """Execute agents concurrently with their respective contexts.

        Args:
            contexts: mapping from agent_id to AgentContext

        Returns:
            SwarmResult containing successful AgentResult objects and error strings.
        """
        if len(self.agents) != len(contexts):
            raise SwarmError("Number of agents and contexts must match")

        tasks = {}
        for agent in self.agents:
            ctx = contexts.get(agent.agent_id)
            if ctx is None:
                raise SwarmError(f"Missing context for agent {agent.agent_id}")
            tasks[agent.agent_id] = (agent, ctx)

        result = SwarmResult()
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_map = {}
            for agent_id, (agent, ctx) in tasks.items():
                future = executor.submit(agent.run, ctx)
                future_map[future] = agent_id

            for future in as_completed(future_map):
                agent_id = future_map[future]
                try:
                    agent_result = future.result()
                    result.results.append(agent_result)
                except Exception as e:
                    result.errors[agent_id] = str(e)

        return result
