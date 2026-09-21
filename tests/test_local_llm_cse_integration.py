from __future__ import annotations

import importlib.util
import os
from collections.abc import Callable, Mapping
from typing import Any

import pytest

from egregore.application.constrained_semantic_engine import (
    ConstrainedSemanticEngine,
    SemanticCandidate,
)
from egregore.application.dossier_generate_service import (
    DossierGenerateRequest,
    DossierGenerateService,
)
from egregore.application.in_memory_dossier_adapters import (
    AllowAllAuthzProvider,
    InMemoryCaseStore,
    InMemoryIdempotencyStore,
    InMemoryTransactionalPersistence,
)
from egregore.application.semantics_executor import (
    CorePlaneGenerateDossierExecutor,
    GenerateDossierEngineResult,
)
from egregore.domain.semantics_models import CaseState, GenerateDossierCommand
from egregore.infrastructure.local_llm_adapter import LocalLlmAdapter

GGUF_DEFAULT_PATH = "~/models/qwen2.5-1.5b-instruct-q4_k_m.gguf"


def _llama_cpp_available() -> bool:
    return importlib.util.find_spec("llama_cpp") is not None


def _model_path_from_env() -> str:
    return os.path.expanduser(
        os.environ.get("EGREGORE_LOCAL_LLM_MODEL_PATH", GGUF_DEFAULT_PATH)
    )


def _extract_candidate_lines(llm_text: str) -> list[str]:
    """
    Deterministic candidate extraction:
    - split by lines
    - keep non-empty
    - strip
    """
    lines: list[str] = []
    for raw_line in llm_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # allow simple prefix formats like "1) ..." or "- ..."
        line = line.lstrip("-•0123456789). ").strip()
        if line:
            lines.append(line)
    return lines


def build_compute_engine_policy(
    *, adapter: LocalLlmAdapter, cse: ConstrainedSemanticEngine
) -> Callable[[GenerateDossierCommand], GenerateDossierEngineResult]:
    def _prompt_for_ir(cmd: GenerateDossierCommand) -> str:
        # Keep prompt deterministic and constrain output away from legal-conclusion phrasing.
        raw = cmd.input_payload.get("raw")
        if not isinstance(raw, str):
            raw = str(cmd.input_payload)

        return (
            "You are producing evidence-bounded interpretations ONLY.\n"
            "Avoid legal conclusions (e.g., no 'establishes liability', 'proves wrongdoing', or similar).\n"
            "Output 3 alternative interpretations, each on its own line, starting with 'May indicate:'\n\n"
            f"Observed notes:\n{raw}\n"
        )

    def _policy(cmd: GenerateDossierCommand) -> GenerateDossierEngineResult:
        prompt = _prompt_for_ir(cmd)
        out = adapter.generate(prompt=prompt, max_tokens=96, temperature=0.0)

        candidates = _extract_candidate_lines(out["text"])
        # Ensure non-empty candidate list even if model output is unexpected.
        # This fallback candidate is deliberately evidence-bounded and avoids forbidden phrases.
        if not candidates:
            candidates = [
                "May indicate: the provided notes are relevant to the described facts."
            ]

        semantic_candidates: list[SemanticCandidate] = [
            SemanticCandidate(
                raw_text=c,
                normalized_text=c,
                confidence=1.0,
                metadata={},
            )
            for c in candidates
        ]

        collapsed = cse.collapse(semantic_candidates)

        # Shape engine_out.data for Layer-0 canonical IR deserialization.
        # - classification_layer.routing must be a string (fed into ClassificationStatement)
        # - interpretation_layer.statements is a list[str] for EvidenceInterpretationStatement
        engine_data: dict[str, Any] = {
            "classification_layer": {
                "routing": collapsed.classification,
                "confidence": 1.0,
            },
            "interpretation_layer": {
                "statements": [collapsed.canonical_text],
            },
            # No fact_layer needed for minimal guarded pipeline.
        }

        engine_metadata: Mapping[str, Any] = {
            "input_fingerprint": cmd.input_fingerprint,
            # Deterministically propagate model hashes to payload for auditability.
            "model_hash": out["model_hash"],
            "prompt_hash": out["prompt_hash"],
            "output_hash": out["output_hash"],
            "collapsed_semantic_hash": collapsed.semantic_hash,
        }

        return GenerateDossierEngineResult(
            data=engine_data, metadata=dict(engine_metadata)
        )

    return _policy


@pytest.mark.skipif(not _llama_cpp_available(), reason="llama-cpp-python not installed")
@pytest.mark.skipif(
    not os.path.exists(_model_path_from_env()),
    reason="GGUF model not present at EGREGORE_LOCAL_LLM_MODEL_PATH",
)
def test_local_llm_cse_end_to_end_governed_inference() -> None:
    model_path = _model_path_from_env()

    # Plane 2 inference adapter
    adapter = LocalLlmAdapter(model_path, seed=42)
    cse = ConstrainedSemanticEngine()

    compute_policy = build_compute_engine_policy(adapter=adapter, cse=cse)

    # Plane 1 deterministic core plumbing (CPU-only in-memory stores)
    case_store = InMemoryCaseStore()
    case_store.seed(
        organization_id="org_1",
        case_id="case_1",
        state=CaseState.active,
        next_version=1,
    )

    idempotency = InMemoryIdempotencyStore()
    tx = InMemoryTransactionalPersistence(
        idempotency=idempotency, case_store=case_store
    )

    executor = CorePlaneGenerateDossierExecutor(
        authz=AllowAllAuthzProvider(),
        case_store=case_store,
        idempotency_store=idempotency,
        transactional_persistence=tx,
        compute_engine_policy=compute_policy,
    )

    service = DossierGenerateService(executor=executor)

    req = DossierGenerateRequest(
        organization_id="org_1",
        case_id="case_1",
        actor_id="actor_api_key_1",
        input_fingerprint="fp-local-llm-cse-1",
        engine_version="local_llm_engine_v0",
        policy_version="policy_v1",
        input_payload={
            "raw": "I need a one-sentence evidence-based interpretation of these notes: the claimant alleges X."
        },
        causality_id="cmd-local-llm-cse-1",
        request_id="req-local-llm-cse-1",
        timestamp_ns=None,  # deterministic derive by service
    )

    ack = service.generate(request=req)
    assert ack.http_status == 200
    assert ack.outbox_ids is not None
    assert tx.commit_count == 1
