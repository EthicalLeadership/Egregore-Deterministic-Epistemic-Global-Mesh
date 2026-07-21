from __future__ import annotations

import pytest

from egregore.application.semantics_executor import (
    GenerateDossierEngineResult,
)
from egregore.application.semantics_replay_interpreter import (
    CorePlaneReplayInterpreter,
)
from egregore.domain.legal_agent.projection_registry import StaticProjectionRegistry
from egregore.domain.semantics.derivations import derive_generate_artifacts
from egregore.domain.semantics.domain_adapters import DossierSemanticsDomainAdapter
from egregore.domain.semantics.ir_deserialization import deserialize_to_canonical_ir
from egregore.domain.semantics.reasoning_guard import (
    enforce_evidence_to_conclusion_boundary,
)
from egregore.domain.semantics_models import (
    GenerateDossierCommand,
)


def test_reasoning_guard_reject_only_forbidden_evidence_to_conclusion_phrasing() -> (
    None
):
    payload = {
        "fact_layer": {"f1": "some fact"},
        "classification_layer": {"routing": "ALLOW", "confidence": 0.5},
        "interpretation_layer": {
            "statements": [
                "This establishes liability based on the provided evidence.",
                "This may indicate a risk pattern.",
            ]
        },
        "excluded_layer": {},
    }

    with pytest.raises(ValueError) as excinfo:
        enforce_evidence_to_conclusion_boundary(payload)

    assert "Forbidden evidence-to-conclusion phrasing" in str(excinfo.value)


def _make_command(
    *,
    fingerprint: str = "fp-1",
    engine_version: str = "engine_vA",
    policy_version: str = "policy_v1",
) -> GenerateDossierCommand:
    return GenerateDossierCommand(
        organization_id="org_1",
        case_id="case_1",
        actor_id="actor_api_key_1",
        input_fingerprint=fingerprint,
        engine_version=engine_version,
        policy_version=policy_version,
        input_payload={"raw": "messy legal notes"},
        causality_id="cmd-1",
        request_id="req-1",
    )


def test_replay_bounded_invariance_allows_structural_snapshot_drift_preserving_pi() -> (
    None
):
    """
    Regression test for Gate 5 bounded invariance:

    Previously, replay failed if `dict(snapshot_data) != derived_snapshot`.
    This treats representational drift (e.g., list vs tuple) as a semantic failure.

    Gate 5 says replay truth is π_O / ≡_O only, so this test introduces drift:
    - committed snapshot uses tuple for `statements`
    - derived snapshot uses list for `statements`
    The JSON canonicalization used by PI_O should serialize tuple/list identically,
    so π_O must remain equivalent.
    """
    command = _make_command()

    # Deterministic compute output that deserialize_to_canonical_ir understands.
    engine_out = GenerateDossierEngineResult(
        data={
            "fact_layer": {"s1": "workplace communications facts"},
            "classification_layer": {"routing": "ALLOW", "confidence": 0.9},
            "interpretation_layer": {
                "statements": ["This may indicate comms-related risk."]
            },
        },
        metadata={"input_fingerprint": command.input_fingerprint},
    )

    def compute_engine_policy(
        cmd: GenerateDossierCommand,
    ) -> GenerateDossierEngineResult:
        assert cmd.input_fingerprint == command.input_fingerprint
        return engine_out

    timestamp_ns = 123
    version_number = 1
    version_id = "vid-1"
    reasoning_version_id = "reasoning-v1"

    # Derived canonical snapshot (what replay would deterministically reconstruct).
    canonical_ir = deserialize_to_canonical_ir(
        untrusted_payload=dict(engine_out.data),
        version_id=f"ir-v1-{command.engine_version}",
        reasoning_version_id=reasoning_version_id,
    )
    derived_snapshot = canonical_ir.to_dict()

    # Introduce representational drift: list -> tuple in snapshot_data.
    committed_snapshot_data = dict(derived_snapshot)
    committed_snapshot_data["statements"] = tuple(committed_snapshot_data["statements"])

    # Build deterministic artifacts to satisfy event/outbox identity checks.
    artifacts = derive_generate_artifacts(
        command=command,
        timestamp_ns=timestamp_ns,
        version_id=version_id,
        version_number=version_number,
        engine_data=derived_snapshot,  # executor-like: commits canonical_ir.to_dict()
        engine_metadata={
            "execution_path": [
                "INIT",
                "VALIDATE",
                "PLAN",
                "EXECUTE",
                "VERIFY",
                "COMMIT",
            ],
            "task_id": command.causality_id,
            "canonical_ir_version": canonical_ir.version_id,
            "reasoning_version_id": canonical_ir.reasoning_version_id,
            "input_fingerprint": command.input_fingerprint,
        },
        event_schema_version="v0",
        event_seqs=(0, 1),
        domain_adapter=DossierSemanticsDomainAdapter(),
    )

    interpreter = CorePlaneReplayInterpreter(
        compute_engine_policy=compute_engine_policy,
        projection_descriptors=StaticProjectionRegistry().all_descriptors(),
        overlap_classifications=StaticProjectionRegistry().all_overlap_classifications(),
    )

    result = interpreter.replay_equivalence(
        command=command,
        timestamp_ns=timestamp_ns,
        version_id=version_id,
        snapshot_data=committed_snapshot_data,
        events=artifacts.events,
        outbox_entries=artifacts.outbox_entries,
        projection_version="pcl-v1",
    )

    assert (
        result.ok
    ), f"Expected replay to succeed under representational drift; failures={result.failures}"
