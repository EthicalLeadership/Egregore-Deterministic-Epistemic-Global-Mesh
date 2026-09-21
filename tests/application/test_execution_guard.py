"""Tests for ExecutionGuard, ExecutionContext, and StructuredFailure."""

import pytest

from egregore.application.execution_guard import ExecutionGuard
from egregore.application.feature_flag_registry import FeatureFlag, FeatureFlagRegistry
from egregore.application.guard_policy import GuardPolicy, GuardPolicyError
from egregore.domain.execution_context import ExecutionContext
from egregore.domain.execution_record import BudgetContext, PolicyContext
from egregore.domain.structured_failure import StructuredFailure


class TestExecutionGuardSuccess:
    def test_execute_returns_handler_result(self):
        ctx = ExecutionContext(
            tenant_id="t1",
            user_id="u1",
            role="admin",
            session_id="s1",
            trace_id="tr1",
            subsystem="test",
            operation="add",
        )
        result = ExecutionGuard.execute(ctx, lambda a, b: a + b, 2, 3)
        assert result == 5

    def test_execute_with_kwargs(self):
        ctx = ExecutionContext(
            tenant_id="t1",
            user_id="u1",
            role="admin",
            session_id="s1",
            trace_id="tr2",
            subsystem="test",
            operation="concat",
        )
        result = ExecutionGuard.execute(
            ctx, lambda x, sep=" ": sep.join(x), ["a", "b"], sep="-"
        )
        assert result == "a-b"


class TestExecutionGuardFailure:
    def test_execute_propagates_exception(self):
        ctx = ExecutionContext(
            tenant_id="t1",
            user_id="u1",
            role="admin",
            session_id="s1",
            trace_id="tr3",
            subsystem="test",
            operation="fail",
        )
        with pytest.raises(RuntimeError, match="boom"):
            ExecutionGuard.execute(
                ctx, lambda: (_ for _ in ()).throw(RuntimeError("boom"))
            )


class TestStructuredFailure:
    def test_from_exception(self):
        try:
            raise ValueError("something broke")
        except Exception as e:
            failure = StructuredFailure.from_exception(e, "test", "op")
            assert failure.subsystem == "test"
            assert failure.operation == "op"
            assert failure.message == "something broke"
            assert failure.severity == "high"
            assert len(failure.failure_id) == 36

    def test_immutable(self):
        f = StructuredFailure.from_exception(RuntimeError("x"), "a", "b")
        with pytest.raises(AttributeError):
            f.message = "changed"


class TestHashDeterminism:
    def test_same_input_same_hash(self):
        h1 = ExecutionGuard._hash_payload({"b": 2, "a": 1})
        h2 = ExecutionGuard._hash_payload({"a": 1, "b": 2})
        assert h1 == h2
        assert len(h1) == 64

    def test_different_input_different_hash(self):
        h1 = ExecutionGuard._hash_payload({"a": 1})
        h2 = ExecutionGuard._hash_payload({"a": 2})
        assert h1 != h2


class TestGuardPolicy:
    def _context(self, **overrides):
        defaults = {
            "tenant_id": "t1",
            "user_id": "u1",
            "role": "admin",
            "session_id": "s1",
            "trace_id": "tr-guard",
            "subsystem": "test",
            "operation": "guarded_op",
        }
        defaults.update(overrides)
        return ExecutionContext(**defaults)

    def _policy(self):
        return GuardPolicy(
            allowed_roles={"admin", "operator"},
            allowed_tenants={"t1"},
            feature_flag_check=lambda name: name == "enabled-flag",
            budget_provider=lambda ctx: BudgetContext(
                budget_id="budget-1", pre_balance=100, post_balance=100
            ),
            policy_provider=lambda ctx: PolicyContext(
                policy_version="v1.0", engine_version="v1.0"
            ),
        )

    def test_identity_rejection_on_missing_user(self):
        ctx = self._context(user_id="")
        with pytest.raises(GuardPolicyError):
            self._policy().validate_identity(ctx)

    def test_role_rejection_for_unauthorized_role(self):
        ctx = self._context(role="guest")
        with pytest.raises(GuardPolicyError):
            self._policy().validate_role(ctx)

    def test_policy_context_returned(self):
        ctx = self._context()
        policy_context = self._policy().validate_policy(ctx)
        assert policy_context.policy_version == "v1.0"

    def test_budget_reserves_cost(self):
        ctx = self._context()
        budget = self._policy().validate_budget(ctx, estimated_cost=10)
        assert budget.pre_balance == 100
        assert budget.post_balance == 90
        assert budget.cost_units == 10

    def test_budget_rejection_when_insufficient(self):
        ctx = self._context()
        with pytest.raises(GuardPolicyError):
            self._policy().validate_budget(ctx, estimated_cost=200)

    def test_feature_flag_enabled(self):
        ctx = self._context()
        self._policy().validate_feature_flag(ctx, flag_name="enabled-flag")

    def test_feature_flag_disabled(self):
        ctx = self._context()
        with pytest.raises(GuardPolicyError):
            self._policy().validate_feature_flag(ctx, flag_name="disabled-flag")

    def test_execute_with_policy_passes(self):
        ctx = self._context()
        policy = self._policy()
        result = ExecutionGuard.execute(
            ctx, lambda a, b: a + b, 2, 3, guard_policy=policy
        )
        assert result == 5

    def test_execute_with_policy_rejects_bad_role(self):
        ctx = self._context(role="guest")
        policy = self._policy()
        with pytest.raises(GuardPolicyError):
            ExecutionGuard.execute(ctx, lambda: None, guard_policy=policy)


class TestFeatureFlagRegistry:
    def test_flag_enabled_globally(self):
        registry = FeatureFlagRegistry({"f1": FeatureFlag("f1", enabled=True)})
        assert registry.is_enabled("f1") is True

    def test_flag_disabled(self):
        registry = FeatureFlagRegistry({"f1": FeatureFlag("f1", enabled=False)})
        assert registry.is_enabled("f1") is False

    def test_flag_tenant_scoped(self):
        registry = FeatureFlagRegistry(
            {
                "f1": FeatureFlag("f1", enabled=True, allowed_tenants=("t1",)),
            }
        )
        assert registry.is_enabled("f1", tenant_id="t1") is True
        assert registry.is_enabled("f1", tenant_id="t2") is False

    def test_flag_role_scoped(self):
        registry = FeatureFlagRegistry(
            {
                "f1": FeatureFlag("f1", enabled=True, allowed_roles=("admin",)),
            }
        )
        assert registry.is_enabled("f1", role="admin") is True
        assert registry.is_enabled("f1", role="guest") is False
