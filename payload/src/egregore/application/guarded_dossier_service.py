"""GuardedDossierService — ExecutionGuard wrapper around DossierGenerateService."""

from typing import Any

from egregore.application.execution_guard import ExecutionGuard
from egregore.domain.execution_context import ExecutionContext


class GuardedDossierService:
    """Facade: every DossierGenerateService call passes through ExecutionGuard."""

    def __init__(self, inner_service: Any, context: ExecutionContext):
        self._inner = inner_service
        self._context = context

    def generate(self, command: Any) -> Any:
        return ExecutionGuard.execute(
            context=self._context,
            handler=self._inner.generate,
            command=command,
        )

    def commit_generate_t2(self, dossier: Any) -> Any:
        return ExecutionGuard.execute(
            context=ExecutionContext(
                tenant_id=self._context.tenant_id,
                user_id=self._context.user_id,
                role=self._context.role,
                session_id=self._context.session_id,
                trace_id=self._context.trace_id,
                subsystem="persistence",
                operation="commit_generate_t2",
                metadata={"dossier_id": getattr(dossier, "dossier_id", None)},
            ),
            handler=self._inner.commit_generate_t2,
            dossier=dossier,
        )

    def get_dossier(self, dossier_id: str) -> Any:
        return ExecutionGuard.execute(
            context=ExecutionContext(
                tenant_id=self._context.tenant_id,
                user_id=self._context.user_id,
                role=self._context.role,
                session_id=self._context.session_id,
                trace_id=self._context.trace_id,
                subsystem="persistence",
                operation="get_dossier",
                metadata={"dossier_id": dossier_id},
            ),
            handler=self._inner.get_dossier,
            dossier_id=dossier_id,
        )
