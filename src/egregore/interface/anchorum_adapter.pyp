"""AnchorumAdapter – wraps real deterministic/epistemic Egregore engines.

This adapter is the only boundary that may call:
- LegalReasoningEngine.analyze
- reproducible_fusion

It returns RawAnchorumOutput objects for audit. It never modifies
the underlying engines or matter state, and never makes decisions.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from egregore.application.legal_reasoning_engine import LegalReasoningEngine
from egregore.rfe.engine import reproducible_fusion


class RawAnchorumOutput(BaseModel):
    """Immutable record of a single Anchorum tool invocation."""
    id: str
    tool_name: str
    input_hash: str
    output_hash: str
    raw_payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provenance: Dict[str, str] = Field(default_factory=dict)

    model_config = {"frozen": True}


class AnchorumAdapter:
    """Thread-safe adapter for calling Egregore deterministic functions."""

    def __init__(self, raw_output_dir: Optional[Path] = None):
        self.raw_output_dir = raw_output_dir or Path("anchorum_raw_outputs")
        self.raw_output_dir.mkdir(parents=True, exist_ok=True)
        self._engine = LegalReasoningEngine()

    def _store_raw_output(self, raw: RawAnchorumOutput) -> None:
        """Append raw output to audit log file."""
        log_file = self.raw_output_dir / "raw_outputs.jsonl"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(raw.model_dump_json() + "\n")

    def _compute_hash(self, data: Any) -> str:
        """Return SHA-256 hash of canonical JSON representation."""
        canonical = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _call_tool(
        self,
        tool_name: str,
        func: Callable,
        *args: Any,
        **kwargs: Any,
    ) -> RawAnchorumOutput:
        """Generic call wrapper with retry, hashing, and audit logging."""
        # Serialize inputs for hash
        input_payload = {"args": args, "kwargs": kwargs}
        input_hash = self._compute_hash(input_payload)

        start = time.perf_counter()
        try:
            result = func(*args, **kwargs)
        except Exception as e:
            # Re-raise after logging attempt? Tenacity will retry.
            raise

        # Serialize output (handle Pydantic models, dicts, etc.)
        if hasattr(result, "model_dump"):
            output_payload = result.model_dump()
        elif isinstance(result, dict):
            output_payload = result
        else:
            output_payload = {"result": str(result)}

        output_hash = self._compute_hash(output_payload)

        raw = RawAnchorumOutput(
            id=f"raw-{int(time.time() * 1000)}-{tool_name}",
            tool_name=tool_name,
            input_hash=input_hash,
            output_hash=output_hash,
            raw_payload={
                "input": input_payload,
                "output": output_payload,
                "elapsed_seconds": time.perf_counter() - start,
            },
            provenance={
                "adapter_version": "phase3-1.0",
                "retry_attempts": "1",  # simplified; will be updated if needed
            },
        )

        self._store_raw_output(raw)
        return raw

    def run_legal_analysis(self, ir: Any, case_id: str) -> RawAnchorumOutput:
        """Wrap LegalReasoningEngine.analyze (deterministic legal reasoning)."""
        return self._call_tool(
            tool_name="legal_analysis",
            func=self._engine.analyze,
            ir=ir,
            case_id=case_id,
        )

    def run_reproducible_fusion(
        self,
        manifest: Dict[str, Any],
        config: Optional[Dict[str, Any]] = None,
    ) -> RawAnchorumOutput:
        """Wrap reproducible_fusion (deterministic evidence fusion)."""
        return self._call_tool(
            tool_name="reproducible_fusion",
            func=reproducible_fusion,
            manifest=manifest,
            config=config,
        )
