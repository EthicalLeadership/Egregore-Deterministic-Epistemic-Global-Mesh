from __future__ import annotations

from egregore.domain.semantics.derivations import derive_generate_artifacts
from egregore.domain.semantics_models import GenerateDossierCommand
from egregore.interface.semantics_ports import ISemanticsDomainAdapter


def test_generate_dossier_command_maps_to_strict_replayable_task_contract() -> None:
    command = GenerateDossierCommand(
        organization_id="org_1",
        case_id="case_1",
        actor_id="actor_1",
        input_fingerprint="fp-1",
        engine_version="engine_vA",
        policy_version="policy_v1",
        input_payload={"raw": "notes"},
        causality_id="cmd-1",
        request_id="req-1",
    )

    contract = command.to_task_contract()

    assert contract.task_id == "cmd-1"
    assert contract.intent == "generate_dossier"
    assert contract.constraints == ("deterministic", "fail_closed", "idempotent")
    assert contract.allowed_tools == ()
    assert contract.policy_level == "strict"
    assert contract.expected_outputs == ("snapshot", "audit_events", "outbox_entries")
    assert contract.replayable is True
    assert contract.inputs["organization_id"] == "org_1"
    assert contract.inputs["case_id"] == "case_1"


class DerivationAdapter(ISemanticsDomainAdapter):
    def requested_event_type(self) -> str:
        return "REQ"

    def generated_event_type(self) -> str:
        return "GEN"

    def outbox_side_effect_type(self) -> str:
        return "SIDE"

    def outbox_payload(self, *, engine_data, generated_event_type):
        return {"event": generated_event_type, "data": dict(engine_data)}


def test_derivations_accept_domain_adapter_override() -> None:
    command = GenerateDossierCommand(
        organization_id="org_1",
        case_id="case_1",
        actor_id="actor_1",
        input_fingerprint="fp-1",
        engine_version="engine_vA",
        policy_version="policy_v1",
        input_payload={"raw": "notes"},
        causality_id="cmd-1",
        request_id="req-1",
    )

    artifacts = derive_generate_artifacts(
        command=command,
        timestamp_ns=123,
        version_id="ver_1",
        version_number=1,
        engine_data={"x": 1},
        engine_metadata={},
        event_schema_version="v0",
        domain_adapter=DerivationAdapter(),
    )

    assert artifacts.events[0].event_type == "REQ"
    assert artifacts.events[1].event_type == "GEN"
    assert artifacts.outbox_entries[0].side_effect_type == "SIDE"
    assert artifacts.outbox_entries[0].payload["event"] == "GEN"
