from __future__ import annotations

import pytest

from egregore.factory.schemas.task_envelope import (
    CreateTaskRequest,
    ForensicGate,
    Priority,
    SourceType,
    TaskEnvelope,
    TaskPayload,
    TaskProvenance,
    TaskType,
)


def test_envelope_default_values():
    envelope = TaskEnvelope(source=TaskProvenance(source_type=SourceType.API), payload=TaskPayload())
    assert envelope.task_type == TaskType.UNKNOWN
    assert envelope.priority == Priority.NORMAL
    assert envelope.forensic_gate == ForensicGate.PENDING
    assert envelope.context_budget == 0
    assert envelope.tags == []


def test_envelope_fingerprint_is_stable():
    envelope = TaskEnvelope(
        source=TaskProvenance(source_type=SourceType.API),
        payload=TaskPayload(text="hello", filename="doc.pdf"),
    )
    fp1 = envelope.fingerprint()
    fp2 = envelope.fingerprint()
    assert fp1 == fp2
    assert len(fp1) == 64


def test_envelope_fingerprint_changes_with_payload():
    e1 = TaskEnvelope(source=TaskProvenance(source_type=SourceType.API), payload=TaskPayload(text="a"))
    e2 = TaskEnvelope(source=TaskProvenance(source_type=SourceType.API), payload=TaskPayload(text="b"))
    assert e1.fingerprint() != e2.fingerprint()


def test_envelope_adds_provenance():
    envelope = TaskEnvelope(source=TaskProvenance(source_type=SourceType.API), payload=TaskPayload())
    updated = envelope.add_provenance("intake", "normalized", {"source": "api"})
    assert len(updated.provenance_chain) == 1
    assert updated.provenance_chain[0]["station"] == "intake"
    assert updated.provenance_chain[0]["detail"]["source"] == "api"


def test_create_task_request_defaults():
    req = CreateTaskRequest(source_type=SourceType.CHAT, text="hi")
    assert req.priority == Priority.NORMAL
    assert req.context_budget == 0


def test_context_budget_must_be_non_negative():
    with pytest.raises(ValueError):
        TaskEnvelope(
            source=TaskProvenance(source_type=SourceType.API),
            payload=TaskPayload(),
            context_budget=-1,
        )
