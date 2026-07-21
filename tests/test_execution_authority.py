from __future__ import annotations

import threading

import pytest

from egregore.application.legal_reasoning_engine import LegalReasoningEngine
from egregore.domain.legal_agent.execution_authority import ExecutionAuthority
from egregore.domain.legal_agent.legal_models import LegalAgentVersion
from egregore.domain.legal_agent.rule_registry import StaticRuleRegistry
from egregore.domain.semantics.canonical_ir import CanonicalSemanticIR, FactStatement


def _make_engine() -> LegalReasoningEngine:
    return LegalReasoningEngine(
        rule_registry=StaticRuleRegistry(),
        agent_version=LegalAgentVersion(
            rule_registry_version="v1.0", inference_engine_version="v1.0"
        ),
    )


def _make_ir() -> CanonicalSemanticIR:
    return CanonicalSemanticIR(
        version_id="ir-auth-001",
        reasoning_version_id="rr-auth-001",
        statements=(FactStatement(content="Email was sent.", source_id="s1"),),
    )


def test_analyze_fails_closed_when_ungoverned() -> None:
    engine = _make_engine()
    ir = _make_ir()

    with pytest.raises(RuntimeError, match="Ungoverned execution path blocked"):
        engine.analyze(ir, case_id="case-auth-ungoverned")


def test_analyze_succeeds_when_governed() -> None:
    engine = _make_engine()
    ir = _make_ir()

    with ExecutionAuthority.governed():
        result = engine.analyze(ir, case_id="case-auth-governed")

    assert result.case_id == "case-auth-governed"


def test_governed_scope_does_not_leak_to_other_thread() -> None:
    engine = _make_engine()
    ir = _make_ir()

    worker_err: list[Exception] = []

    with ExecutionAuthority.governed():

        def _worker() -> None:
            try:
                engine.analyze(ir, case_id="case-auth-thread")
            except Exception as exc:  # expected RuntimeError
                worker_err.append(exc)

        t = threading.Thread(target=_worker)
        t.start()
        t.join()

    assert worker_err, "worker must fail closed outside governed scope"
    assert isinstance(worker_err[0], RuntimeError)
    assert "Ungoverned execution path blocked" in str(worker_err[0])
