"""SEL-X guard policy validators.

Pure functions that enforce identity, role, policy, budget, and feature-flag
gates before execution. All validators are side-effect free and raise
``SemanticsError`` on failure.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from egregore.domain.execution_context import ExecutionContext
from egregore.domain.execution_record import BudgetContext, PolicyContext
from egregore.domain.semantics_models import SemanticsError, StableErrorCode


class GuardPolicyError(SemanticsError):
    """Raised when a guard policy check fails."""

    def __init__(
        self, message: str, *, code: StableErrorCode = StableErrorCode.UNAUTHORIZED
    ):
        super().__init__(code=code, message=message)


class GuardPolicy:
    """Configurable SEL-X pre-flight policy for ExecutionGuard.

    Each validate_* method is a pure predicate. The executor calls them in
    order before invoking the handler.
    """

    def __init__(
        self,
        *,
        allowed_roles: Sequence[str] | None = None,
        allowed_tenants: Sequence[str] | None = None,
        feature_flag_check: Callable[[str], bool] | None = None,
        budget_provider: Callable[[str], BudgetContext] | None = None,
        policy_provider: Callable[[Any], PolicyContext] | None = None,
    ) -> None:
        self._allowed_roles = set(allowed_roles) if allowed_roles else None
        self._allowed_tenants = set(allowed_tenants) if allowed_tenants else None
        self._feature_flag_check = feature_flag_check
        self._budget_provider = budget_provider
        self._policy_provider = policy_provider

    def validate_identity(
        self,
        context: ExecutionContext,
        *,
        required_user_id: str | None = None,
    ) -> None:
        """Ensure the execution context carries a non-empty identity."""
        if not context.user_id or not context.session_id or not context.trace_id:
            raise GuardPolicyError(
                "Execution context missing required identity fields",
                code=StableErrorCode.VALIDATION_FAILED,
            )
        if required_user_id is not None and context.user_id != required_user_id:
            raise GuardPolicyError(
                f"User identity mismatch: expected {required_user_id}",
                code=StableErrorCode.UNAUTHORIZED,
            )

    def validate_role(self, context: ExecutionContext) -> None:
        """Ensure the principal's role is allowed."""
        if not context.role:
            raise GuardPolicyError(
                "Execution context missing role",
                code=StableErrorCode.VALIDATION_FAILED,
            )
        if self._allowed_roles is not None and context.role not in self._allowed_roles:
            raise GuardPolicyError(
                f"Role '{context.role}' is not authorized for this operation",
                code=StableErrorCode.FORBIDDEN_STATE_TRANSITION,
            )

    def validate_policy(
        self,
        context: ExecutionContext,
        *,
        required_policy_version: str | None = None,
    ) -> PolicyContext:
        """Resolve and validate the policy context for execution."""
        if self._policy_provider is None:
            raise GuardPolicyError(
                "No policy provider configured",
                code=StableErrorCode.VALIDATION_FAILED,
            )
        policy_context = self._policy_provider(context)
        if (
            required_policy_version is not None
            and policy_context.policy_version != required_policy_version
        ):
            raise GuardPolicyError(
                f"Policy version mismatch: expected {required_policy_version}",
                code=StableErrorCode.FORBIDDEN_STATE_TRANSITION,
            )
        return policy_context

    def validate_budget(
        self,
        context: ExecutionContext,
        *,
        estimated_cost: int = 0,
    ) -> BudgetContext:
        """Reserve budget and return atomic budget context."""
        if self._budget_provider is None:
            raise GuardPolicyError(
                "No budget provider configured",
                code=StableErrorCode.VALIDATION_FAILED,
            )
        budget_context = self._budget_provider(context)
        if budget_context.pre_balance < estimated_cost:
            raise GuardPolicyError(
                f"Insufficient budget: {budget_context.pre_balance} < {estimated_cost}",
                code=StableErrorCode.RATE_LIMITED,
            )
        return BudgetContext(
            budget_id=budget_context.budget_id,
            pre_balance=budget_context.pre_balance,
            post_balance=budget_context.pre_balance - estimated_cost,
            cost_units=estimated_cost,
            currency=budget_context.currency,
        )

    def validate_feature_flag(
        self,
        context: ExecutionContext,
        *,
        flag_name: str,
    ) -> None:
        """Ensure a feature flag is enabled for the tenant/principal."""
        if self._feature_flag_check is None:
            raise GuardPolicyError(
                "No feature flag check configured",
                code=StableErrorCode.VALIDATION_FAILED,
            )
        if not self._feature_flag_check(flag_name):
            raise GuardPolicyError(
                f"Feature flag '{flag_name}' is disabled",
                code=StableErrorCode.FORBIDDEN_STATE_TRANSITION,
            )

    def validate_all(
        self,
        context: ExecutionContext,
        *,
        estimated_cost: int = 0,
        required_feature_flag: str | None = None,
    ) -> tuple[PolicyContext, BudgetContext | None]:
        """Run the full SEL-X checklist and return resolved contexts."""
        self.validate_identity(context)
        self.validate_role(context)
        policy_context = self.validate_policy(context)
        budget_context = self.validate_budget(context, estimated_cost=estimated_cost)
        if required_feature_flag is not None:
            self.validate_feature_flag(context, flag_name=required_feature_flag)
        return policy_context, budget_context
