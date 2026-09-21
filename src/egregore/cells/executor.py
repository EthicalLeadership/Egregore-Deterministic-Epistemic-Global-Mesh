"""Cell pipeline executor.

Runs the staged pipeline declared in a cell spec deterministically:

1. Topologically sort stages by ``depends_on``.
2. Render each stage's prompt template against the running context.
3. Call the configured GGUF model or deterministic tool.
4. Extract structured output (JSON/code/text) and feed it downstream.
5. Return a ``CellResult`` with provenance hash and RFE-ready metadata.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel, Field

from egregore.cells.model_host import ModelHost, resolve_model_specs
from egregore.cells.models import CellSpec, Stage
from egregore.cells.registry import CellRegistry
from egregore.cells.tools import TOOLS
from egregore.shared.canonical import canonical_loads
from egregore.tooling.deterministic_verification import canonical_dumps

logger = logging.getLogger("egregore.cells.executor")


def _extract_json(text: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction from model output (fenced or raw)."""
    text = text.strip()

    fence_match = re.search(
        r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE
    )
    if fence_match:
        candidate = fence_match.group(1).strip()
        try:
            return cast(dict[str, Any], canonical_loads(candidate))
        except (json.JSONDecodeError, ValueError):
            pass

    raw_match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if raw_match:
        try:
            return cast(dict[str, Any], canonical_loads(raw_match.group(1)))
        except (json.JSONDecodeError, ValueError):
            pass

    try:
        return cast(dict[str, Any], canonical_loads(text))
    except (json.JSONDecodeError, ValueError):
        return None


def _topological_sort(stages: list[Stage]) -> list[Stage]:
    """Return stages ordered by dependencies."""
    by_id = {s.stage_id: s for s in stages}
    visited: set[str] = set()
    ordered: list[Stage] = []

    def visit(stage: Stage) -> None:
        if stage.stage_id in visited:
            return
        for dep in stage.depends_on:
            if dep not in by_id:
                raise ValueError(
                    f"stage '{stage.stage_id}' depends on unknown stage '{dep}'"
                )
            visit(by_id[dep])
        visited.add(stage.stage_id)
        ordered.append(stage)

    for s in stages:
        visit(s)
    return ordered


def _render_prompt(template: str | None, context: dict[str, Any]) -> str:
    if template is None:
        return ""
    try:
        return template.format(**context)
    except KeyError as exc:
        raise ValueError(f"prompt template missing placeholder: {exc}") from exc


def _call_llm(
    llm: Any,
    prompt: str,
    system: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> tuple[str, int]:
    """Run a single chat completion and return text + token count."""
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs: dict[str, Any] = {}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if temperature is not None:
        kwargs["temperature"] = temperature

    response = llm.create_chat_completion(messages=messages, **kwargs)
    choice = response["choices"][0]
    content = choice["message"].get("content", "")
    usage = response.get("usage", {})
    tokens = usage.get("total_tokens", usage.get("completion_tokens", 0))
    return content.strip(), int(tokens)


class StageOutput(BaseModel):
    """Result of executing one pipeline stage."""

    stage_id: str
    output: str
    parsed: dict[str, Any] | None = None
    model: str | None = None
    tool: str | None = None
    tokens: int = 0
    elapsed_ms: float = 0.0


class CellResult(BaseModel):
    """Result of executing a cell pipeline."""

    cell_id: str
    cell_type: str
    tier: int
    taxonomy: str
    request: dict[str, Any]
    stages: dict[str, StageOutput]
    final_output: dict[str, Any] | str
    verdict: str = "PASS"
    confidence: float = 0.5
    elapsed_ms: float = 0.0
    provenance_hash: str
    executed_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class CellExecutor:
    """Execute cell specs against a model host."""

    def __init__(
        self,
        registry: CellRegistry | None = None,
        model_host: ModelHost | None = None,
    ) -> None:
        self.registry = registry or CellRegistry()
        self._model_host = model_host

    def _get_model_host(self, spec: CellSpec) -> ModelHost:
        if self._model_host is not None:
            return self._model_host
        specs = resolve_model_specs([m.model_dump(by_alias=True) for m in spec.models])
        return ModelHost(model_specs=specs)

    def run(self, cell_id: str, request: dict[str, Any]) -> CellResult:
        """Execute the cell identified by ``cell_id`` with the supplied request."""
        start = time.monotonic()
        spec = self.registry.get(cell_id)
        host = self._get_model_host(spec)

        context: dict[str, Any] = dict(request)
        context["input"] = request.get("input", "")
        stages: dict[str, StageOutput] = {}

        for stage in _topological_sort(spec.pipeline.stages):
            stage_start = time.monotonic()

            if stage.tool is not None:
                output, parsed, tokens = self._run_tool_stage(stage, context)
                model = None
                tool = stage.tool
            else:
                output, parsed, tokens = self._run_llm_stage(stage, context, host)
                model = stage.model
                tool = None

            elapsed = round((time.monotonic() - stage_start) * 1000, 2)
            stages[stage.stage_id] = StageOutput(
                stage_id=stage.stage_id,
                output=output,
                parsed=parsed,
                model=model,
                tool=tool,
                tokens=tokens,
                elapsed_ms=elapsed,
            )

            # Make downstream placeholders available.
            context[f"{stage.stage_id}_output"] = output
            if parsed:
                for key, value in parsed.items():
                    context[key] = value

        final_output = self._derive_final_output(spec, context, stages)
        verdict = self._derive_verdict(stages)
        confidence = self._derive_confidence(stages, verdict)
        elapsed_ms = round((time.monotonic() - start) * 1000, 2)

        provenance = {
            "cell_id": spec.cell_id,
            "version": spec.version,
            "taxonomy": spec.taxonomy_path(),
            "request": request,
            "stages": {sid: s.model_dump() for sid, s in stages.items()},
            "verdict": verdict,
            "executed_at": datetime.now(UTC).isoformat(),
        }
        provenance_hash = hashlib.sha256(
            canonical_dumps(provenance).encode()
        ).hexdigest()

        return CellResult(
            cell_id=spec.cell_id,
            cell_type=spec.type,
            tier=spec.tier,
            taxonomy=spec.taxonomy_path(),
            request=request,
            stages=stages,
            final_output=final_output,
            verdict=verdict,
            confidence=confidence,
            elapsed_ms=elapsed_ms,
            provenance_hash=provenance_hash,
        )

    def _run_llm_stage(
        self,
        stage: Stage,
        context: dict[str, Any],
        host: ModelHost,
    ) -> tuple[str, dict[str, Any] | None, int]:
        if stage.model is None:
            raise ValueError(f"LLM stage '{stage.stage_id}' has no model")
        llm = host.get(stage.model)
        prompt = _render_prompt(stage.prompt, context)
        system = stage.system
        max_tokens = stage.max_tokens
        temperature = stage.temperature if stage.temperature is not None else 0.0

        output, tokens = _call_llm(
            llm,
            prompt=prompt,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        parsed = _extract_json(output) if stage.output_format == "json" else None
        return output, parsed, tokens

    def _run_tool_stage(
        self,
        stage: Stage,
        context: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None, int]:
        if stage.tool is None:
            raise ValueError(f"tool stage '{stage.stage_id}' has no tool")
        tool_fn = TOOLS.get(stage.tool)
        if tool_fn is None:
            raise ValueError(f"unknown tool '{stage.tool}'")

        result = tool_fn(stage, context)
        if isinstance(result, dict):
            output = result.get("output", canonical_dumps(result))
            parsed = result if stage.output_format == "json" else None
        else:
            output = str(result)
            parsed = _extract_json(output) if stage.output_format == "json" else None
        return output, parsed, 0

    def _derive_final_output(
        self,
        spec: CellSpec,
        context: dict[str, Any],
        stages: dict[str, StageOutput],
    ) -> dict[str, Any] | str:
        """Build the cell's final output from the running context."""
        # If the last stage produced JSON, prefer it.
        last_stage = list(stages.values())[-1] if stages else None
        if last_stage and last_stage.parsed:
            return last_stage.parsed

        # If known code artifacts exist in context, bundle them.
        artifacts: dict[str, Any] = {}
        for key in (
            "module",
            "test",
            "readme",
            "module_code",
            "test_code",
            "readme_code",
        ):
            if key in context:
                artifacts[key] = context[key]
        if artifacts:
            return artifacts

        # Fallback: last stage raw output.
        return last_stage.output if last_stage else ""

    def _derive_verdict(self, stages: dict[str, StageOutput]) -> str:
        """Derive overall verdict from JSON verdict fields in stage outputs."""
        for stage in reversed(stages.values()):
            parsed = stage.parsed
            if isinstance(parsed, dict):
                verdict = str(parsed.get("verdict", "")).upper()
                if verdict in {"PASS", "FAIL"}:
                    return verdict
        return "PASS"

    def _derive_confidence(self, stages: dict[str, StageOutput], verdict: str) -> float:
        """Simple confidence heuristic based on stage success and verdict."""
        if not stages:
            return 0.0
        scores: list[float] = []
        for stage in stages.values():
            if stage.parsed is not None:
                scores.append(0.9)
            elif stage.tool:
                scores.append(1.0)
            else:
                scores.append(0.7)
        base = sum(scores) / len(scores)
        return round(base * (0.95 if verdict == "PASS" else 0.75), 2)
