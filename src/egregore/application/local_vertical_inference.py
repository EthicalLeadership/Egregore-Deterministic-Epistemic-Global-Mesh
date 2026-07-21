from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from egregore.application.constrained_semantic_engine import (
    ConstrainedSemanticEngine,
    SemanticCandidate,
)
from egregore.application.semantics_executor import GenerateDossierEngineResult
from egregore.domain.semantics_models import GenerateDossierCommand

if TYPE_CHECKING:
    from egregore.infrastructure.local_model_catalog import LocalModelCatalog


class TextGenerationAdapter(Protocol):
    def generate(
        self,
        *,
        prompt: str,
        max_tokens: int = 128,
        temperature: float = 0.0,
    ) -> Mapping[str, str]: ...


def _extract_candidate_lines(llm_text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in llm_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = line.lstrip("-•0123456789). ").strip()
        if line:
            lines.append(line)
    return lines


def _default_prompt(*, vertical: str, raw_notes: str) -> str:
    return (
        "You are producing evidence-bounded interpretations ONLY.\n"
        "Avoid legal conclusions (e.g., no 'establishes liability', 'proves wrongdoing', or similar).\n"
        f"Vertical context: {vertical}.\n"
        "Output 3 alternative interpretations, each on its own line, starting with 'May indicate:'\n\n"
        f"Observed notes:\n{raw_notes}\n"
    )


@dataclass(frozen=True)
class VerticalInferenceConfig:
    vertical: str
    speed_tier: str = "fast"
    max_tokens: int = 96
    temperature: float = 0.0


def build_vertical_compute_engine_policy(
    *,
    catalog: LocalModelCatalog,
    cse: ConstrainedSemanticEngine,
    config: VerticalInferenceConfig,
    adapter_factory: (
        Callable[[LocalModelCatalog, str, str, str], TextGenerationAdapter] | None
    ) = None,
) -> Callable[[GenerateDossierCommand], GenerateDossierEngineResult]:
    """
    Build policy function that selects a local on-disk model by vertical + policy_version.

    - model routing is fail-closed (delegated to LocalModelCatalog)
    - output is collapsed through ConstrainedSemanticEngine for policy-safe semantics
    """

    def _default_adapter_factory(
        cat: LocalModelCatalog,
        vertical: str,
        policy_version: str,
        speed_tier: str,
    ) -> TextGenerationAdapter:
        return cat.build_adapter(
            vertical=vertical,
            policy_version=policy_version,
            speed_tier=speed_tier,
        )

    factory = adapter_factory or _default_adapter_factory

    def _policy(cmd: GenerateDossierCommand) -> GenerateDossierEngineResult:
        adapter = factory(
            catalog, config.vertical, cmd.policy_version, config.speed_tier
        )

        raw = cmd.input_payload.get("raw")
        if not isinstance(raw, str):
            raw = str(cmd.input_payload)

        prompt = _default_prompt(vertical=config.vertical, raw_notes=raw)
        out = adapter.generate(
            prompt=prompt,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )

        candidates = _extract_candidate_lines(str(out.get("text", "")))
        if not candidates:
            candidates = [
                "May indicate: the provided notes are relevant to the described facts."
            ]

        semantic_candidates = [
            SemanticCandidate(
                raw_text=c, normalized_text=c, confidence=1.0, metadata={}
            )
            for c in candidates
        ]
        collapsed = cse.collapse(semantic_candidates)

        engine_data: dict[str, Any] = {
            "classification_layer": {
                "routing": collapsed.classification,
                "confidence": 1.0,
            },
            "interpretation_layer": {
                "statements": [collapsed.canonical_text],
            },
        }

        metadata = {
            "input_fingerprint": cmd.input_fingerprint,
            "vertical": config.vertical,
            "speed_tier": config.speed_tier,
            "model_hash": str(out.get("model_hash", "")),
            "prompt_hash": str(out.get("prompt_hash", "")),
            "output_hash": str(out.get("output_hash", "")),
            "collapsed_semantic_hash": collapsed.semantic_hash,
            "candidate_count": str(collapsed.candidate_count),
            "forbidden_dropped_count": str(collapsed.forbidden_dropped_count),
        }
        return GenerateDossierEngineResult(data=engine_data, metadata=metadata)

    return _policy
