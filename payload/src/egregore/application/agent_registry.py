"""Agent registry — discover and describe CLI agents in a directory."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from egregore.shared.canonical import canonical_loads

DEFAULT_AGENT_DIR = Path(__file__).resolve().parents[3] / "agents"


@dataclass(frozen=True)
class AgentSpec:
    """Metadata for a CLI agent."""

    name: str
    path: Path
    description: str = ""
    timeout: float = 120.0
    allowed_roles: frozenset[str] = field(
        default_factory=lambda: frozenset({"admin", "operator"})
    )


class AgentRegistry:
    """
    Discover CLI agents in a directory.

    An agent is any executable file in the agent directory. Optional sidecar
    manifests (`<agent>.json`) provide metadata without executing the binary.
    """

    def __init__(self, agent_dir: Path | str | None = None) -> None:
        self.agent_dir = Path(
            agent_dir or os.environ.get("EGREGORE_AGENTS_DIR", DEFAULT_AGENT_DIR)
        )
        self._agents: dict[str, AgentSpec] = {}
        self.discover()

    def discover(self) -> None:
        """Scan the agent directory and populate the registry."""
        self._agents.clear()
        if not self.agent_dir.exists():
            return

        for entry in self.agent_dir.iterdir():
            if entry.is_dir():
                continue
            if not os.access(entry, os.X_OK):
                # Skip non-executable files unless they have a manifest declaring them executable.
                manifest_path = Path(str(entry) + ".json")
                if not manifest_path.exists():
                    continue
            name = entry.name
            manifest_path = Path(str(entry) + ".json")
            manifest = self._load_manifest(manifest_path)
            self._agents[name] = AgentSpec(
                name=name,
                path=entry,
                description=manifest.get("description", ""),
                timeout=float(manifest.get("timeout", 120.0)),
                allowed_roles=frozenset(
                    manifest.get("allowed_roles", ["admin", "operator"])
                ),
            )

    @staticmethod
    def _load_manifest(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return canonical_loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def get(self, name: str) -> AgentSpec | None:
        return self._agents.get(name)

    def list_agents(self) -> list[AgentSpec]:
        return sorted(self._agents.values(), key=lambda a: a.name)

    def __contains__(self, name: str) -> bool:
        return name in self._agents

    def __len__(self) -> int:
        return len(self._agents)
