"""Dossier Agent.

Searches and reads case workspace files, queries the case RAG index,
and invokes legal analysis through the Anchorum adapter. It returns
structured findings but never decides or releases documents.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from egregore.application.agents.base import BaseAgent, AgentContext, AgentResult
from egregore.interface.anchorum_adapter import AnchorumAdapter
from egregore.assurance.assurance_engine import AssuranceEngine

logger = logging.getLogger(__name__)

MAX_READ_BYTES = 50_000


class DossierAgent(BaseAgent):
    """Agent that explores a dossier and produces structured findings."""

    agent_id = "dossier"
    version = "0.1.0"

    def __init__(
        self,
        workspace_root: Path,
        adapter: AnchorumAdapter,
        assurance: AssuranceEngine,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.workspace_root = workspace_root.resolve()
        self.adapter = adapter
        self.assurance = assurance

    def list_files(self, extension: Optional[str] = None) -> List[str]:
        """Return relative paths of all files in the workspace."""
        files: List[str] = []
        for p in sorted(self.workspace_root.rglob("*")):
            if p.is_file():
                rel = p.relative_to(self.workspace_root).as_posix()
                if extension and not rel.endswith(extension):
                    continue
                files.append(rel)
        return files

    def read_file(self, relative_path: str) -> str:
        """Read a text file from the workspace, capped to MAX_READ_BYTES."""
        full = (self.workspace_root / relative_path).resolve()
        if not str(full).startswith(str(self.workspace_root)):
            raise ValueError("Path escapes workspace")
        if not full.is_file():
            raise FileNotFoundError(f"File not found: {relative_path}")
        data = full.read_bytes()[:MAX_READ_BYTES]
        try:
            return data.decode("utf-8", errors="replace")
        except Exception:
            return data.decode("latin-1", errors="replace")

    def search_rag(self, query: str, case_id: str) -> List[Dict[str, Any]]:
        """Query the per-case RAG index if available; otherwise fallback to file scan."""
        try:
            from egregore.interface.case_rag import query_case_index
            return query_case_index(case_id, query, top_k=4)
        except Exception:
            logger.warning("RAG query unavailable; falling back to file scan")
            results = []
            for rel in self.list_files():
                try:
                    content = self.read_file(rel)
                    if query.lower() in content.lower():
                        results.append({"path": rel, "chunk": content[:300]})
                except Exception:
                    continue
                if len(results) >= 5:
                    break
            return results

    def run_legal_analysis(self, ir: Any, case_id: str) -> AgentResult:
        """Run legal analysis and assurance on a CanonicalSemanticIR."""
        run_id = hashlib.sha256(f"{case_id}:{time.time_ns()}".encode()).hexdigest()[:16]

        raw = self.adapter.run_legal_analysis(ir=ir, case_id=case_id)
        output = raw.raw_payload["output"]

        # Build minimal epistemic graph
        from egregore.domain.epistemic import EpistemicGraph
        graph = EpistemicGraph(
            case_id=case_id,
            evidences=(),
            propositions=(),
            contradictions=(),
            provenance_metadata={"raw_output_id": raw.id},
        )
        report = self.assurance.run(graph)

        return AgentResult(
            agent_id=self.agent_id,
            run_id=run_id,
            findings={
                "case_id": case_id,
                "raw_output_id": raw.id,
                "uncertainty_flags": output.get("uncertainty_flags", []),
                "assurance_status": report.overall_status.value,
            },
            raw_anchorum_outputs={"legal_analysis": raw.model_dump()},
            assurance_report=report.model_dump() if report else None,
        )

    def run(self, context: AgentContext) -> AgentResult:
        """Execute dossier exploration based on context.raw_inputs."""
        action = context.raw_inputs.get("action", "list")
        run_id = hashlib.sha256(f"{context.matter_id}:{time.time_ns()}".encode()).hexdigest()[:16]

        findings: Dict[str, Any] = {"matter_id": context.matter_id, "action": action}

        if action == "list_files":
            findings["files"] = self.list_files()
        elif action == "read_file":
            rel = context.raw_inputs.get("path")
            if not rel:
                raise ValueError("read_file requires 'path'")
            findings["content"] = self.read_file(rel)
        elif action == "search":
            query = context.raw_inputs.get("query")
            case_id = context.raw_inputs.get("case_id")
            if not query or not case_id:
                raise ValueError("search requires 'query' and 'case_id'")
            findings["results"] = self.search_rag(query, case_id)
        elif action == "analyze":
            ir = context.raw_inputs.get("ir")
            case_id = context.raw_inputs.get("case_id")
            if ir is None or not case_id:
                raise ValueError("analyze requires 'ir' and 'case_id'")
            return self.run_legal_analysis(ir, case_id)
        else:
            raise ValueError(f"Unknown action: {action}")

        return AgentResult(
            agent_id=self.agent_id,
            run_id=run_id,
            findings=findings,
            raw_anchorum_outputs={},
        )
