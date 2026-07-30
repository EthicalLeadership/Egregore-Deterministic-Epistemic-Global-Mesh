from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Any

from egregore.application.container import EgregoreContainer


def create_app(build_container: bool = True) -> Any:
    """
    Create the FastAPI app.

    FastAPI is an optional dependency in this repo's execution environment.
    If FastAPI isn't installed, this function raises a clear error.

    Args:
        build_container: When True (production), bootstrap the full Egregore DI
            container and attach its inference service to app.state.

    """
    try:
        from fastapi import FastAPI
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "FastAPI is not installed in this environment. "
            "Install `fastapi` and `uvicorn` to run the HTTP API."
        ) from exc

    # Lazy imports so the module can be imported without FastAPI installed.
    import sys

    # Clear stale module cache for v1 routers (they may have been imported before FastAPI was available)
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("egregore.http_api.http.v1."):
            del sys.modules[mod_name]
    from egregore.ems.proxy import build_proxy_router
    from egregore.http_api.http.middleware.api_key_middleware import APIKeyMiddleware
    from egregore.http_api.http.v1.auth import router as auth_router
    from egregore.http_api.http.v1.chat import router as chat_router
    from egregore.http_api.http.v1.code_factory import router as code_factory_router
    from egregore.http_api.http.v1.dossiers import router as dossiers_router
    from egregore.http_api.http.v1.federation import router as federation_router
    from egregore.http_api.http.v1.intake import router as intake_router
    from egregore.http_api.http.v1.rfe import router as rfe_router
    from egregore.http_api.http.v1.users import router as users_router
    from egregore.http_api.http.v1.workflows import router as workflows_router
    from egregore.http_api.http.v1.ws_chat import router as ws_chat_router
    from egregore.interface.anchorum_router import ingest_router
    from egregore.interface.anchorum_router import router as anchorum_router
    from egregore.interface.factory_router import router as factory_router
    from egregore.interface.ombudsman_router import router as ombudsman_router
    from egregore.interface.rag_api import router as rag_router

    app = FastAPI(title="Egregore API")
    app.add_middleware(APIKeyMiddleware)

    # CORS: allow ANCHORUM frontend (port 4173) and local dev servers.
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:4173",
            "http://localhost:4173",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://localhost:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Bootstrap DI container and expose inference service to routers.
    if build_container:
        try:
            container = EgregoreContainer.from_env()
            app.state.inference_service = container.inference_service
            app.state.code_factory = container.code_factory
        except Exception as exc:  # noqa: BLE001
            warnings.warn(
                f"Failed to bootstrap EgregoreContainer: {exc}. "
                "Chat endpoints will fall back to Egregore EMS.",
                RuntimeWarning,
                stacklevel=2,
            )
            app.state.inference_service = None

    # Defensive: skip None routers (modules may return None when FastAPI is missing)
    _routers = [
        dossiers_router,
        workflows_router,
        ws_chat_router,
        auth_router,
        users_router,
        intake_router,
        chat_router,
        code_factory_router,
    ]
    for router in _routers:
        if router is not None:
            app.include_router(router)

    # EMS Proxy router — unified model inference endpoint (sovereign Egregore backend).
    # When the main Egregore process already loaded a native CoderBackend, share it
    # with the proxy so only one copy of the model sits in GPU memory.
    try:
        from egregore.ems.registry import build_registry_from_env

        ems_registry = build_registry_from_env()
        native_backend = None
        if app.state.inference_service is not None:
            native_backend = app.state.inference_service.clients.get("egregore")
        ems_proxy_router = build_proxy_router(ems_registry, native_backend=native_backend)
        app.include_router(ems_proxy_router)
    except Exception as exc:  # noqa: BLE001
        warnings.warn(
            f"Failed to mount EMS proxy router: {exc}. "
            "Unified inference endpoint will not be available.",
            RuntimeWarning,
            stacklevel=2,
        )

    # RAG query router for the Legal Dossier knowledge base.
    if rag_router is not None:
        app.include_router(rag_router)

    # Federation treaty and entropy exchange router.
    if federation_router is not None:
        app.include_router(federation_router)

    # The RFE router declares paths relative to its mount point.
    if rfe_router is not None:
        app.include_router(rfe_router, prefix="/api/v1/rfe")

    # The factory router declares paths relative to its mount point.
    if factory_router is not None:
        app.include_router(factory_router, prefix="/api/v1/factory")

    # The ombudsman router declares its own /api/v1/ombudsman prefix.
    if ombudsman_router is not None:
        app.include_router(ombudsman_router)

    # ANCHORUM forensic frontend bridge.
    if anchorum_router is not None:
        app.include_router(anchorum_router)

    # ANCHORUM Stage-4 ingest endpoint (mounted at root so the connector can POST /ingest).
    if ingest_router is not None:
        app.include_router(ingest_router)

    # Serve vertical pages from the repo's `static/` folder:
    #   /services/<vertical>/  ->  static/<vertical>/index.html
    from fastapi.staticfiles import StaticFiles

    # Serve a specific PDF from an external on-disk path.
    # Override with EGREGORE_NETWORK_ISOLATION_PDF_PATH env var.
    network_isolation_pdf_path = Path(
        os.environ.get(
            "EGREGORE_NETWORK_ISOLATION_PDF_PATH",
            os.environ.get("DOWNLOADS_DIR", "/opt/egregore/downloads")
            + "/network_isolation_hardening.pdf",
        )
    )

    from fastapi import HTTPException as FastAPIHTTPException
    from fastapi.responses import FileResponse

    @app.get("/services/network_isolation_hardening.pdf", include_in_schema=False)
    async def _serve_network_isolation_hardening_pdf() -> Any:
        if not network_isolation_pdf_path.exists():
            raise FastAPIHTTPException(
                status_code=404,
                detail="network_isolation_hardening.pdf not found",
            )
        return FileResponse(
            str(network_isolation_pdf_path),
            media_type="application/pdf",
            filename="network_isolation_hardening.pdf",
        )

    # NOTE: This route must be registered *before* mounting StaticFiles at "/services",
    # otherwise the StaticFiles mount shadows this endpoint.
    repo_root = Path(
        os.environ.get("EGREGORE_REPO_ROOT", Path(__file__).resolve().parents[4])
    )
    deploy_dir = repo_root / "static"
    if deploy_dir.exists():
        app.mount(
            "/services",
            StaticFiles(directory=str(deploy_dir), html=True),
            name="services",
        )
    else:
        warnings.warn(
            f"static/ directory not found at {repo_root}/static. "
            "Static file serving for vertical pages is disabled.",
            RuntimeWarning,
            stacklevel=2,
        )

    @app.get("/health", include_in_schema=False)
    async def _health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", include_in_schema=False)
    async def _ready() -> Any:
        """Readiness probe: verifies connectivity to Postgres, Redis, NATS, and LLM backends."""
        import os

        from fastapi.responses import JSONResponse

        checks: dict[str, Any] = {}
        healthy = True

        db_url = os.environ.get("EGREGORE_DB_URL", "")
        if db_url.startswith("postgresql") or db_url.startswith("postgres+"):
            try:
                import asyncpg

                # asyncpg expects a plain postgres DSN, not the SQLAlchemy asyncpg variant.
                driverless_url = db_url.replace(
                    "postgresql+asyncpg", "postgresql"
                ).replace("postgres+asyncpg", "postgresql")
                conn = await asyncpg.connect(driverless_url)
                value = await conn.fetchval("SELECT 1")
                await conn.close()
                checks["db"] = "ok" if value == 1 else f"unexpected: {value}"
            except Exception as exc:  # noqa: BLE001
                checks["db"] = f"error: {exc}"
                healthy = False
        else:
            checks["db"] = "skipped"

        redis_url = os.environ.get("REDIS_URL", "")
        if redis_url:
            try:
                import redis.asyncio as aioredis

                r = aioredis.from_url(redis_url)
                await r.ping()
                await r.close()
                checks["redis"] = "ok"
            except Exception as exc:  # noqa: BLE001
                checks["redis"] = f"error: {exc}"
                healthy = False
        else:
            checks["redis"] = "skipped"

        nats_url = os.environ.get("NATS_URL", "")
        if nats_url:
            try:
                import nats

                nc = await nats.connect(nats_url)
                await nc.close()
                checks["nats"] = "ok"
            except Exception as exc:  # noqa: BLE001
                checks["nats"] = f"error: {exc}"
                healthy = False
        else:
            checks["nats"] = "skipped"

        # LLM backend health (best-effort; not required for readiness unless explicitly configured)
        inference_service = getattr(app.state, "inference_service", None)
        if inference_service is not None:
            try:
                llm_health = inference_service.health()
                checks["llm"] = llm_health
                if not any(
                    b.get("reachable") for b in llm_health.get("backends", {}).values()
                ):
                    checks["llm_reachable"] = "no backends reachable"
            except Exception as exc:  # noqa: BLE001
                checks["llm"] = f"error: {exc}"
        else:
            checks["llm"] = "not configured"

        status_code = 200 if healthy else 503
        return JSONResponse(
            content={"status": "ready" if healthy else "not_ready", "checks": checks},
            status_code=status_code,
        )

    return app
