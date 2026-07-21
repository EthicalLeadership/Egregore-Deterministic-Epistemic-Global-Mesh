from __future__ import annotations

import contextlib
import os
from typing import Any

from egregore.application.constrained_semantic_engine import ConstrainedSemanticEngine
from egregore.application.dossier_generate_service import (
    DossierGenerateRequest,
    DossierGenerateService,
)
from egregore.application.http_journal_provider import (
    build_http_core_and_edge_journals,
)
from egregore.application.in_memory_dossier_adapters import AllowAllAuthzProvider
from egregore.application.local_vertical_inference import (
    VerticalInferenceConfig,
    build_vertical_compute_engine_policy,
)
from egregore.application.semantics_executor import (
    CorePlaneGenerateDossierExecutor,
    GenerateDossierEngineResult,
)
from egregore.domain.semantics_models import (
    CaseState,
    CommandAck,
    GenerateDossierCommand,
)


def _deterministic_engine_policy() -> Any:
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


_CORE_JOURNAL, _EDGE_JOURNAL = build_http_core_and_edge_journals()

_CSE = ConstrainedSemanticEngine()

_VERTICAL_EXECUTOR_CACHE: dict[
    tuple[str, str, str], CorePlaneGenerateDossierExecutor
] = {}

_DEFAULT_EXECUTOR = CorePlaneGenerateDossierExecutor(
    authz=AllowAllAuthzProvider(),
    case_store=_CORE_JOURNAL,
    idempotency_store=_CORE_JOURNAL,
    transactional_persistence=_CORE_JOURNAL,
    compute_engine_policy=_deterministic_engine_policy(),
)

_DEFAULT_SERVICE = DossierGenerateService(executor=_DEFAULT_EXECUTOR)


def _ensure_case_seeded_in_store(
    *, store: Any, organization_id: str, case_id: str
) -> None:
    """
    Fail-closed seeding helper.

    HTTP endpoints must not encode persistence semantics; this module owns the
    "seed if missing" behavior.
    """
    seeded = False
    with contextlib.suppress(Exception):
        _ = store.get_case_state(organization_id=organization_id, case_id=case_id)
        _ = store.get_next_version_number(
            organization_id=organization_id, case_id=case_id
        )
        seeded = True
    if seeded:
        return

    if hasattr(store, "seed_case"):
        store.seed_case(  # type: ignore[attr-defined]  # optional dependency / compatibility
            organization_id=organization_id,
            case_id=case_id,
            state=CaseState.active,
            next_version=1,
        )
        return

    if hasattr(store, "seed"):
        store.seed(  # type: ignore[attr-defined]  # optional dependency / compatibility
            organization_id=organization_id,
            case_id=case_id,
            state=CaseState.active,
            next_version=1,
        )
        return

    raise RuntimeError("Store is missing case seeding capability")


def _model_manifest_path() -> str | None:
    manifest_path = os.environ.get("BLACKSTAR_LOCAL_MODEL_MANIFEST")
    if not manifest_path:
        return None
    return os.path.expanduser(manifest_path)


def _get_model_catalog() -> object | None:
    manifest_path = _model_manifest_path()
    if manifest_path is None:
        return None

    # Lazy import to avoid paying manifest parse cost unless needed.
    from importlib import import_module

    mod = import_module("egregore.infrastructure.local_model_catalog")
    LocalModelCatalog = mod.LocalModelCatalog  # noqa: N806
    return LocalModelCatalog.from_manifest_file(manifest_path)


def _service_for_vertical(
    *, vertical: str, policy_version: str
) -> DossierGenerateService:
    catalog = _get_model_catalog()

    cache_key = (
        vertical,
        policy_version,
        "edge-fallback" if catalog is None else "model",
    )
    executor = _VERTICAL_EXECUTOR_CACHE.get(cache_key)
    if executor is None:
        if catalog is None:
            compute_policy = _deterministic_engine_policy()
        else:
            compute_policy = build_vertical_compute_engine_policy(
                catalog=catalog,
                cse=_CSE,
                config=VerticalInferenceConfig(vertical=vertical, speed_tier="fast"),
            )

        executor = CorePlaneGenerateDossierExecutor(
            authz=AllowAllAuthzProvider(),
            case_store=_EDGE_JOURNAL,
            idempotency_store=_EDGE_JOURNAL,
            transactional_persistence=_EDGE_JOURNAL,
            compute_engine_policy=compute_policy,
        )
        _VERTICAL_EXECUTOR_CACHE[cache_key] = executor

    return DossierGenerateService(executor=executor)


def generate_dossier_v1(
    *, request: DossierGenerateRequest, vertical: str | None = None
) -> CommandAck:
    """
    Application facade for HTTP v1 dossier generation.

    Interface layer must call this facade rather than constructing executors or journals.
    """
    _ensure_case_seeded_in_store(
        store=_CORE_JOURNAL,
        organization_id=request.organization_id,
        case_id=request.case_id,
    )

    service: DossierGenerateService
    if vertical is None or vertical.strip() == "":
        service = _DEFAULT_SERVICE
    else:
        _ensure_case_seeded_in_store(
            store=_EDGE_JOURNAL,
            organization_id=request.organization_id,
            case_id=request.case_id,
        )
        service = _service_for_vertical(
            vertical=vertical.strip(), policy_version=request.policy_version
        )

    return service.generate(request=request)
