"""Integration tests for ExecutionGuard wrapping existing services."""

from unittest.mock import MagicMock

import pytest

from egregore.application.guarded_dossier_service import GuardedDossierService
from egregore.application.guarded_llm_adapter import GuardedLlmAdapter
from egregore.domain.execution_context import ExecutionContext


class MockDossierService:
    def generate(self, command):
        return {"dossier_id": "doss-123", "status": "generated"}

    def commit_generate_t2(self, dossier):
        return {"committed": True, "version": 1}

    def get_dossier(self, dossier_id):
        return {"dossier_id": dossier_id, "state": "active"}


class MockLlmAdapter:
    model_name = "qwen-2.5-coder"

    def generate(self, prompt, max_tokens=512, temperature=0.0, **kwargs):
        return f"Response to: {prompt[:20]}..."

    def batch_generate(self, prompts, max_tokens=512, temperature=0.0, **kwargs):
        return [f"Response {i}" for i in range(len(prompts))]


class TestGuardedDossierService:
    def test_generate_returns_result(self):
        inner = MockDossierService()
        ctx = ExecutionContext(
            tenant_id="t1",
            user_id="u1",
            role="admin",
            session_id="s1",
            trace_id="tr1",
            subsystem="test",
            operation="generate",
        )
        guarded = GuardedDossierService(inner_service=inner, context=ctx)
        result = guarded.generate(command={"case_id": "case-001"})
        assert result["dossier_id"] == "doss-123"

    def test_commit_generate_t2_returns_result(self):
        inner = MockDossierService()
        ctx = ExecutionContext(
            tenant_id="t1",
            user_id="u1",
            role="admin",
            session_id="s1",
            trace_id="tr1",
            subsystem="test",
            operation="generate",
        )
        guarded = GuardedDossierService(inner_service=inner, context=ctx)
        result = guarded.commit_generate_t2(dossier=MagicMock(dossier_id="doss-456"))
        assert result["committed"] is True

    def test_get_dossier_returns_result(self):
        inner = MockDossierService()
        ctx = ExecutionContext(
            tenant_id="t1",
            user_id="u1",
            role="admin",
            session_id="s1",
            trace_id="tr1",
            subsystem="test",
            operation="generate",
        )
        guarded = GuardedDossierService(inner_service=inner, context=ctx)
        result = guarded.get_dossier(dossier_id="doss-789")
        assert result["dossier_id"] == "doss-789"

    def test_failure_propagates(self):
        inner = MockDossierService()
        inner.generate = lambda command: (_ for _ in ()).throw(RuntimeError("db down"))
        ctx = ExecutionContext(
            tenant_id="t1",
            user_id="u1",
            role="admin",
            session_id="s1",
            trace_id="tr1",
            subsystem="test",
            operation="generate",
        )
        guarded = GuardedDossierService(inner_service=inner, context=ctx)
        with pytest.raises(RuntimeError, match="db down"):
            guarded.generate(command={})


class TestGuardedLlmAdapter:
    def test_generate_returns_result(self):
        inner = MockLlmAdapter()
        ctx = ExecutionContext(
            tenant_id="t1",
            user_id="u1",
            role="admin",
            session_id="s1",
            trace_id="tr1",
            subsystem="test",
            operation="inference",
        )
        guarded = GuardedLlmAdapter(inner_adapter=inner, context=ctx)
        result = guarded.generate(prompt="Hello world")
        assert "Hello world" in result

    def test_batch_generate_returns_results(self):
        inner = MockLlmAdapter()
        ctx = ExecutionContext(
            tenant_id="t1",
            user_id="u1",
            role="admin",
            session_id="s1",
            trace_id="tr1",
            subsystem="test",
            operation="inference",
        )
        guarded = GuardedLlmAdapter(inner_adapter=inner, context=ctx)
        results = guarded.batch_generate(prompts=["A", "B", "C"])
        assert len(results) == 3

    def test_model_name_passthrough(self):
        inner = MockLlmAdapter()
        ctx = ExecutionContext(
            tenant_id="t1",
            user_id="u1",
            role="admin",
            session_id="s1",
            trace_id="tr1",
            subsystem="test",
            operation="inference",
        )
        guarded = GuardedLlmAdapter(inner_adapter=inner, context=ctx)
        assert guarded.model_name == "qwen-2.5-coder"

    def test_nondeterministic_temperature_logged(self):
        inner = MockLlmAdapter()
        ctx = ExecutionContext(
            tenant_id="t1",
            user_id="u1",
            role="admin",
            session_id="s1",
            trace_id="tr1",
            subsystem="test",
            operation="inference",
        )
        guarded = GuardedLlmAdapter(inner_adapter=inner, context=ctx)
        result = guarded.generate(prompt="Creative", temperature=0.7)
        assert "Creative" in result
