"""Egregore Plane 2 (Projection) bootstrap — clean."""

from __future__ import annotations

# Load .env so uvicorn picks up BLACKSTAR_API_KEYS and other config even when
# started directly (not through the desktop launcher).
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import APIRouter, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from egregore.application.agent_registry import AgentRegistry
from egregore.application.inference_service import build_inference_service_from_env
from egregore.http_api.http.middleware.api_key_middleware import APIKeyMiddleware
from egregore.http_api.http.v1.chat import router as chat_router
from egregore.http_api.http.v1.embeddings import router as embeddings_router
from egregore.http_api.http.v1.ws_chat import router as ws_chat_router
from egregore.interface.anchorum_router import ingest_router
from egregore.interface.anchorum_router import router as anchorum_router
from egregore.interface.dashboard import DashboardService, DashboardServiceProvider
from egregore.interface.dashboard import router as dashboard_router
from egregore.interface.dashboard.freeze_middleware import FreezeGateMiddleware
from egregore.interface.factory_router import router as factory_router
from egregore.interface.ombudsman_router import router as ombudsman_router
from egregore.interface.rag_api import router as rag_router
from egregore.shared.freeze_state import FreezeController, FreezeState

logger = logging.getLogger("egregore.bootstrap")


# ---------- Pydantic Models ----------
class NodeHealth(BaseModel):
    node_id: str
    status: str
    host: str
    port: int
    healthy: bool
    latency_ms: float | None = None
    error: str | None = None
    last_seen: float | None = None


class NodesHealthResponse(BaseModel):
    online_count: int
    total: int
    nodes: list[NodeHealth]


class DossierGenerateRequest(BaseModel):
    actor: dict = Field(default_factory=dict)
    causality_id: str = Field(default="default", min_length=1, max_length=256)
    engine_version: str = Field(default="v1", max_length=32)
    policy_version: str = Field(default="v1", max_length=32)
    input_fingerprint: str = Field(default="", max_length=512)
    timestamp_ns: int | None = Field(default=None, ge=0)
    model_config = {"extra": "forbid"}


class HealthResponse(BaseModel):
    status: str
    plane: str
    timestamp: float
    checks: dict[str, Any]


# ---------- Middleware ----------
class SecurityHeadersMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"X-Content-Type-Options", b"nosniff"))
                headers.append((b"X-Frame-Options", b"DENY"))
                headers.append(
                    (
                        b"Strict-Transport-Security",
                        b"max-age=31536000; includeSubDomains",
                    )
                )
                headers.append((b"Referrer-Policy", b"strict-origin-when-cross-origin"))
                if path.startswith("/static"):
                    # Long-term cache only for versioned assets; HTML pages must not be cached
                    # so that chat/dashboard updates reach clients immediately.
                    if path.endswith(".html") or path.endswith("/"):
                        headers.append((b"Cache-Control", b"no-store, must-revalidate"))
                    else:
                        headers.append(
                            (b"Cache-Control", b"public, max-age=31536000, immutable")
                        )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


class AuditLogMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid.uuid4())
        scope["request_id"] = request_id
        start = time.monotonic()
        status_code = 200

        async def capturing_send(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capturing_send)
        finally:
            duration = time.monotonic() - start
            logger.info(
                "request",
                extra={
                    "request_id": request_id,
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": status_code,
                    "duration_ms": round(duration * 1000, 3),
                },
            )


# ---------- Rate Limiter ----------
class RateLimiter:
    def __init__(self, rate: float = 100.0, capacity: int = 200) -> None:
        self.rate = rate
        self.capacity = capacity
        self.tokens: dict[str, tuple[float, float]] = {}

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        tokens, last = self.tokens.get(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + self.rate * (now - last))
        if tokens >= 1.0:
            self.tokens[key] = (tokens - 1.0, now)
            return True
        self.tokens[key] = (tokens, now)
        return False


# ---------- Composition Root ----------
class CompositionRoot:
    def __init__(
        self, freeze_controller: Any | None = None, auth_context: Any | None = None
    ) -> None:
        self.freeze_controller = freeze_controller or FreezeController()
        self.auth_context = auth_context or _StubAuthContext()
        self.rate_limiter = RateLimiter()
        self.node_id = "pioneer1"


class _StubFreezeController:
    state = FreezeState.HEALTHY

    def get_status(self) -> str:
        return "HEALTHY"

    def get_audit_log(self, limit: int = 100) -> list[Any]:
        return []

    def freeze(self, reason: str, operator_id: str, **kwargs: Any) -> None:
        pass

    def unfreeze(self, reason: str, operator_id: str) -> None:
        pass

    def reset(self, reason: str, operator_id: str) -> None:
        pass

    def is_frozen(self) -> bool:
        return False

    @property
    def history(self) -> list[Any]:
        return []


class _StubAuthContext:
    operator_id = "system"
    roles = {"operator", "admin"}


# ---------- App Factory ----------
API_PREFIX = "/api/v1"
HEALTH_PREFIX = "/health"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> Any:
    logger.info("Plane 2 projection layer starting")
    yield
    logger.info("Plane 2 projection layer stopping")


def _all_paths(app: FastAPI) -> list[str]:
    paths = []
    for r in app.routes:
        if hasattr(r, "path") and r.path:
            paths.append(r.path)
        if hasattr(r, "include_context") and hasattr(
            r.include_context, "included_router"
        ):
            for sub in r.include_context.included_router.routes:
                if hasattr(sub, "path") and sub.path:
                    paths.append(sub.path)
    return paths


def create_app(freeze_controller: Any | None = None) -> FastAPI:  # noqa: C901
    app = FastAPI(
        title="Egregore Projection Plane",
        version="0.6.0-phase1",
        lifespan=_lifespan,
        docs_url=None,
        redoc_url=None,
    )

    # Build composition root and store on app state
    root = CompositionRoot(freeze_controller=freeze_controller)
    app.state.composition_root = root

    # Build multi-backend inference service for chat (Ollama, Anthropic, DeepSeek)
    app.state.inference_service = build_inference_service_from_env()

    # Discover CLI agents for chat dispatch
    app.state.agent_registry = AgentRegistry()

    # Security & observability middleware
    app.add_middleware(FreezeGateMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AuditLogMiddleware)
    app.add_middleware(APIKeyMiddleware)
    _trusted_hosts_raw = os.environ.get(
        "BLACKSTAR_TRUSTED_HOSTS", "localhost,127.0.0.1,*.egregore.local,*"
    )
    _trusted_hosts = [h.strip() for h in _trusted_hosts_raw.split(",") if h.strip()]
    if _trusted_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=_trusted_hosts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://egregore.local",
            "http://127.0.0.1:4173",
            "http://localhost:4173",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://localhost:3000",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["X-Request-ID", "Authorization", "Content-Type", "X-API-Key"],
    )

    # Health router
    health_router = APIRouter(prefix=HEALTH_PREFIX)

    @health_router.get("/ready", response_model=HealthResponse)
    async def ready() -> HealthResponse:
        return HealthResponse(
            status="ready", plane="projection", timestamp=time.time(), checks={}
        )

    @health_router.get("/live", response_model=HealthResponse)
    async def live() -> HealthResponse:
        return HealthResponse(
            status="alive", plane="projection", timestamp=time.time(), checks={}
        )

    def _parse_cluster_nodes() -> list[dict[str, Any]]:
        raw = os.environ.get(
            "BLACKSTAR_CLUSTER_NODES",
            "pioneer1=127.0.0.1:8080,pioneer2=192.168.1.102:8000,pioneer3=192.168.1.103:8000",
        )
        nodes: list[dict[str, Any]] = []
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry or "=" not in entry:
                continue
            node_id, hostport = entry.split("=", 1)
            node_id = node_id.strip()
            hostport = hostport.strip()
            if ":" not in hostport:
                continue
            host, port_str = hostport.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                continue
            nodes.append({"node_id": node_id, "host": host, "port": port})
        return nodes

    @health_router.get("/nodes", response_model=NodesHealthResponse)
    async def nodes_health() -> NodesHealthResponse:
        configured = _parse_cluster_nodes()
        results: list[NodeHealth] = []
        online = 0
        async with httpx.AsyncClient(timeout=2.0) as client:
            for cfg in configured:
                node_id = cfg["node_id"]
                host = cfg["host"]
                port = cfg["port"]
                url = f"http://{host}:{port}/health/ready"
                start = time.time()
                try:
                    resp = await client.get(url)
                    latency_ms = (time.time() - start) * 1000
                    if resp.status_code == 200:
                        results.append(
                            NodeHealth(
                                node_id=node_id,
                                status="online",
                                host=host,
                                port=port,
                                healthy=True,
                                latency_ms=round(latency_ms, 2),
                                last_seen=time.time(),
                            )
                        )
                        online += 1
                    else:
                        results.append(
                            NodeHealth(
                                node_id=node_id,
                                status="offline",
                                host=host,
                                port=port,
                                healthy=False,
                                latency_ms=round(latency_ms, 2),
                                error=f"HTTP {resp.status_code}",
                                last_seen=time.time(),
                            )
                        )
                except Exception as exc:
                    msg = str(exc) or "unreachable"
                    results.append(
                        NodeHealth(
                            node_id=node_id,
                            status="offline",
                            host=host,
                            port=port,
                            healthy=False,
                            error=msg,
                            last_seen=time.time(),
                        )
                    )
        return NodesHealthResponse(
            online_count=online, total=len(results), nodes=results
        )

    @health_router.get("/deep")
    async def deep_health() -> JSONResponse:
        root = app.state.composition_root
        checks = {
            "freeze_controller": root.freeze_controller.get_status() is not None,
            "auth_context": root.auth_context.operator_id is not None,
            "rate_limiter": root.rate_limiter is not None,
        }
        all_ok = all(checks.values())
        return JSONResponse(
            content={"status": "healthy" if all_ok else "unhealthy", "checks": checks},
            status_code=(
                status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE
            ),
        )

    app.include_router(health_router)

    # API router (no auth required for dashboard, auth for dossier)
    api_router = APIRouter(prefix=API_PREFIX)

    @api_router.post("/dossier/generate", response_model=dict)
    async def dossier_generate(
        request: DossierGenerateRequest, req: Request
    ) -> JSONResponse | dict[str, Any]:
        root = req.app.state.composition_root
        # Rate limit
        client_key = req.headers.get(
            "X-API-Key", req.client.host if req.client else "unknown"
        )
        if not root.rate_limiter.is_allowed(client_key):
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"error": "Rate limit exceeded"},
            )
        # Stub response for now
        return {"accepted": True, "trace_id": request.timestamp_ns or 0}

    app.include_router(api_router)
    app.include_router(chat_router)
    app.include_router(embeddings_router)
    app.include_router(factory_router, prefix=f"{API_PREFIX}/factory")
    app.include_router(ombudsman_router)
    app.include_router(rag_router)
    app.include_router(anchorum_router)
    app.include_router(ingest_router)

    # Chat WebSocket endpoint (requires api_key cookie/session)
    app.include_router(ws_chat_router)

    # Mount static files and dashboard
    repo_root = Path(__file__).resolve().parents[3]
    static_dir = repo_root / "static"
    app.mount(
        "/static", StaticFiles(directory=str(static_dir), html=True), name="static"
    )
    dashboard_service = DashboardService(
        freeze_controller=root.freeze_controller,  # type: ignore[arg-type]  # justification: runtime may inject a stub FreezeController while DashboardService expects FreezeControllerPort protocol
        auth_context=root.auth_context,
        node_id=root.node_id,
    )
    DashboardServiceProvider.set(dashboard_service)
    app.include_router(dashboard_router)

    # Fail-closed route check
    routes = _all_paths(app)
    required = {
        "/health/ready",
        "/health/live",
        "/health/deep",
        "/api/v1/dossier/generate",
        "/dashboard",
        "/dashboard/login",
    }
    missing = required - set(routes)
    if missing:
        raise RuntimeError(f"Bootstrap fail-closed: missing routes {missing}")
    logger.info(f"Bootstrap complete: {len(routes)} routes registered")
    return app
