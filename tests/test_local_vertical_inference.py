from __future__ import annotations

from collections.abc import Mapping

from egregore.application.constrained_semantic_engine import ConstrainedSemanticEngine
from egregore.application.local_vertical_inference import (
    VerticalInferenceConfig,
    build_vertical_compute_engine_policy,
)
from egregore.domain.semantics_models import GenerateDossierCommand
from egregore.infrastructure.local_model_catalog import LocalModelCatalog


class _FakeAdapter:
    def generate(
        self, *, prompt: str, max_tokens: int = 128, temperature: float = 0.0
    ) -> Mapping[str, str]:
        return {
            "text": "May indicate: the notes suggest an evidence-bounded concern for review.",
            "model_hash": "mhash",
            "prompt_hash": "phash",
            "output_hash": "ohash",
        }


def _command(*, policy_version: str) -> GenerateDossierCommand:
    return GenerateDossierCommand(
        organization_id="org_1",
        case_id="case_1",
        actor_id="actor_1",
        input_fingerprint="fp-1",
        engine_version="engine_v1",
        policy_version=policy_version,
        input_payload={"raw": "Observed notes"},
        causality_id="cmd-1",
        request_id="req-1",
    )


def test_vertical_compute_policy_routes_with_catalog_and_cse(tmp_path) -> None:
    model_file = tmp_path / "legal.gguf"
    model_file.write_bytes(b"legal-model")

    catalog = LocalModelCatalog.from_manifest_dict(
        {
            "models": [
                {
                    "model_id": "legal-fast",
                    "vertical": "legal",
                    "policy_versions": ["policy_v1"],
                    "model_path": str(model_file),
                    "speed_tier": "fast",
                }
            ]
        }
    )

    policy = build_vertical_compute_engine_policy(
        catalog=catalog,
        cse=ConstrainedSemanticEngine(),
        config=VerticalInferenceConfig(vertical="legal", speed_tier="fast"),
        adapter_factory=lambda *_: _FakeAdapter(),
    )

    out = policy(_command(policy_version="policy_v1"))
    assert out.data["classification_layer"]["routing"] == "semantic_projection"
    assert out.metadata["vertical"] == "legal"
    assert out.metadata["model_hash"] == "mhash"


def test_vertical_compute_policy_fails_closed_for_unbound_policy(tmp_path) -> None:
    model_file = tmp_path / "legal.gguf"
    model_file.write_bytes(b"legal-model")

    catalog = LocalModelCatalog.from_manifest_dict(
        {
            "models": [
                {
                    "model_id": "legal-fast",
                    "vertical": "legal",
                    "policy_versions": ["policy_v1"],
                    "model_path": str(model_file),
                    "speed_tier": "fast",
                }
            ]
        }
    )

    policy = build_vertical_compute_engine_policy(
        catalog=catalog,
        cse=ConstrainedSemanticEngine(),
        config=VerticalInferenceConfig(vertical="legal", speed_tier="fast"),
    )

    try:
        policy(_command(policy_version="policy_v2"))
    except Exception as exc:  # fail-closed; concrete type is infra routing error
        assert "policy-compatible model" in str(exc)
    else:
        raise AssertionError("Expected policy routing failure")
