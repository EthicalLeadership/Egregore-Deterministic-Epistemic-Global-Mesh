from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from egregore.application.constrained_semantic_engine import ConstrainedSemanticEngine
from egregore.application.dossier_generate_service import (
    DossierGenerateRequest as AppDossierGenerateRequest,
)
from egregore.application.dossier_generate_service import (
    DossierGenerateService,
)
from egregore.application.http_journal_provider import (
    build_http_core_and_edge_journals,
)
from egregore.application.local_vertical_inference import (
    VerticalInferenceConfig,
    build_vertical_compute_engine_policy,
)
from egregore.application.rbac_authz_provider import RBACAuthzProvider
from egregore.application.semantics_executor import (
    CorePlaneGenerateDossierExecutor,
    GenerateDossierEngineResult,
)
from egregore.domain.semantics_models import CommandAck, GenerateDossierCommand
from egregore.interface.ports.dossier_ports import (
    DossierGenerateRequest,
    DossierServiceFacade,
)


def _deterministic_engine_policy() -> (
    Callable[[GenerateDossierCommand], GenerateDossierEngineResult]
):
    def _policy(cmd: GenerateDossierCommand) -> GenerateDossierEngineResult:
        return GenerateDossierEngineResult(
            data={
                "case_overview": {
                    "engine": cmd.engine_version,
                    "policy": cmd.policy_version,
                },
                "canonical_sections": ["case_overview", "parties", "facts", "timeline"],
            },
            metadata={"input_fingerprint": cmd.input_fingerprint},
        )

    return _policy


@dataclass(frozen=True)
class _FacadeState:
    core_journal: Any
    edge_journal: Any
    cse: ConstrainedSemanticEngine
    authz_provider: Any
    core_service: DossierGenerateService
    vertical_executor_cache: dict[
        tuple[str, str, str], CorePlaneGenerateDossierExecutor
    ]


class DossierServiceFacadeImpl(DossierServiceFacade):
    def __init__(self, *, state: _FacadeState) -> None:
        self._state = state

    def _ensure_case_seeded_in_store(
        self, *, store: Any, request: DossierGenerateRequest
    ) -> None:
        seeded = False
        with contextlib.suppress(Exception):
            _ = store.get_case_state(
                organization_id=request.organization_id, case_id=request.case_id
            )
            _ = store.get_next_version_number(
                organization_id=request.organization_id, case_id=request.case_id
            )
            seeded = True
        if seeded:
            return

        from egregore.domain.semantics_models import CaseState

        if hasattr(store, "seed_case"):
            store.seed_case(  # type: ignore[attr-defined]  # optional dependency / compatibility
                organization_id=request.organization_id,
                case_id=request.case_id,
                state=CaseState.active,
                next_version=1,
            )
            return

        if hasattr(store, "seed"):
            store.seed(  # type: ignore[attr-defined]  # optional dependency / compatibility
                organization_id=request.organization_id,
                case_id=request.case_id,
                state=CaseState.active,
                next_version=1,
            )
            return

        raise RuntimeError("Store is missing case seeding capability")

    def _model_manifest_path(self) -> str | None:
        import os

        manifest_path = os.environ.get("BLACKSTAR_LOCAL_MODEL_MANIFEST")
        if not manifest_path:
            return None
        return os.path.expanduser(manifest_path)

    def _get_model_catalog(self) -> object | None:
        manifest_path = self._model_manifest_path()
        if manifest_path is None:
            return None

        from importlib import import_module

        mod = import_module("egregore.infrastructure.local_model_catalog")
        LocalModelCatalog = mod.LocalModelCatalog  # noqa: N806
        return LocalModelCatalog.from_manifest_file(manifest_path)

    def _service_for_vertical(
        self, *, vertical: str, policy_version: str
    ) -> DossierGenerateService:
        catalog = self._get_model_catalog()

        cache_key = (
            vertical,
            policy_version,
            "edge-fallback" if catalog is None else "model",
        )
        executor = self._state.vertical_executor_cache.get(cache_key)
        if executor is None:
            if catalog is None:
                compute_policy = _deterministic_engine_policy()
            else:
                compute_policy = build_vertical_compute_engine_policy(
                    catalog=catalog,
                    cse=self._state.cse,
                    config=VerticalInferenceConfig(
                        vertical=vertical, speed_tier="fast"
                    ),
                )

            executor = CorePlaneGenerateDossierExecutor(
                authz=self._state.authz_provider,
                case_store=self._state.edge_journal,
                idempotency_store=self._state.edge_journal,
                transactional_persistence=self._state.edge_journal,
                compute_engine_policy=compute_policy,
            )
            self._state.vertical_executor_cache[cache_key] = executor

        return DossierGenerateService(executor=executor)

    def generate(self, *, request: DossierGenerateRequest) -> CommandAck:
        self._ensure_case_seeded_in_store(
            store=self._state.core_journal, request=request
        )

        vertical = request.vertical
        if vertical is None or str(vertical).strip() == "":
            service = self._state.core_service
        else:
            self._ensure_case_seeded_in_store(
                store=self._state.edge_journal, request=request
            )
            service = self._service_for_vertical(
                vertical=str(vertical).strip(), policy_version=request.policy_version
            )

        app_request = AppDossierGenerateRequest(
            organization_id=request.organization_id,
            case_id=request.case_id,
            actor_id=request.actor_id,
            input_fingerprint=request.input_fingerprint,
            engine_version=request.engine_version,
            policy_version=request.policy_version,
            input_payload=request.input_payload,
            causality_id=request.causality_id,
            request_id=request.request_id,
            timestamp_ns=request.timestamp_ns,
        )

        return service.generate(request=app_request)


# Facade factory
_FACADE: _FacadeState | None = None
_FACADE_INSTANCE: DossierServiceFacadeImpl | None = None


def build_dossier_facade() -> DossierServiceFacadeImpl:
    global _FACADE, _FACADE_INSTANCE
    if _FACADE_INSTANCE is not None and _FACADE is not None:
        return _FACADE_INSTANCE

    core_journal, edge_journal = build_http_core_and_edge_journals()
    cse = ConstrainedSemanticEngine()
    # Import USERS from the auth module for RBAC
    try:
        from egregore.http_api.http.v1.auth import USERS
    except ImportError:
        USERS = {}  # noqa: N806
    authz_provider = RBACAuthzProvider(list(USERS.values()))

    core_executor = CorePlaneGenerateDossierExecutor(
        authz=authz_provider,
        case_store=core_journal,
        idempotency_store=core_journal,
        transactional_persistence=core_journal,
        compute_engine_policy=_deterministic_engine_policy(),
    )
    core_service = DossierGenerateService(executor=core_executor)

    state = _FacadeState(
        core_journal=core_journal,
        edge_journal=edge_journal,
        cse=cse,
        authz_provider=authz_provider,
        core_service=core_service,
        vertical_executor_cache={},
    )

    _FACADE = state
    _FACADE_INSTANCE = DossierServiceFacadeImpl(state=state)
    return _FACADE_INSTANCE
