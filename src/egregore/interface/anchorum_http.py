"""Lightweight plain-HTTP app for the ANCHORUM customer-facing site.

Unlike the full bootstrap, this app does NOT load the Egregore native Coder
model. It only serves the dashboard, static assets, and ANCHORUM page. API
calls that need inference should go to the Core API on port 8002 or the HTTPS
bootstrap on port 8443.
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from egregore.http_api.http.middleware.api_key_middleware import APIKeyMiddleware
from egregore.interface.anchorum_router import router as anchorum_router
from egregore.interface.dashboard import DashboardService, DashboardServiceProvider, router as dashboard_router
from egregore.interface.dashboard.freeze_middleware import FreezeGateMiddleware

logger = logging.getLogger("egregore.anchorum_http")

CORE_API_URL = os.environ.get("EGREGORE_CORE_API_URL", "http://127.0.0.1:8002")
CHAT_MODEL = os.environ.get("EGREGORE_CHAT_MODEL", "my-coder-ft")

_LEGAL_SYSTEM = (
    "You are ANCHORUM, a legal dossier assistant for a forensic case workspace. "
    "Answer plainly and cite artifacts by name when possible. If you do not have "
    "enough case context, say what you would need."
)


class ChatIn(BaseModel):
    message: str
    mode: str = "legal"
    case_id: str | None = None


def _case_context(case_id: str) -> str:
    """Build a bounded context block from the real ANCHORUM report on disk."""
    from egregore.interface.anchorum_router import _load_report

    try:
        report = _load_report(case_id)
    except Exception as exc:
        return f"(Case '{case_id}' could not be loaded: {exc})"

    lines = [
        f"CASE: {report.get('case_id', case_id)}",
        f"Artifacts: {report.get('artifact_count', 0)} | "
        f"Entities: {report.get('entity_count', 0)} | "
        f"Anomalies: {report.get('anomaly_count', 0)}",
    ]
    # Keep the context small: the 8-bit model on a 12 GB GPU OOMs on long
    # prompts, so only compact per-finding summaries are included.
    for sev in ("critical", "high"):
        all_f = report.get(f"{sev}_findings", [])
        findings = all_f[:5]
        if findings:
            lines.append(f"\n{sev.upper()} FINDINGS ({len(all_f)} total, showing {len(findings)}):")
            for f in findings:
                desc = str(f.get("description", ""))[:160]
                atype = f.get("anomaly_type", "?")
                lines.append(f"- [{atype}] {desc}")
    med = report.get("medium_findings", [])
    low = report.get("low_findings", [])
    lines.append(f"\nOther findings: {len(med)} medium, {len(low)} low.")
    entities = report.get("entity_directory", [])[:10]
    if entities:
        names = []
        for e in entities:
            if isinstance(e, dict):
                names.append(str(e.get("value") or e.get("entity_value") or e.get("name") or "")[:60])
            else:
                names.append(str(e)[:60])
        lines.append("ENTITIES (sample): " + ", ".join(n for n in names if n))
    return "\n".join(lines)


def _service_api_key() -> str:
    key_file = Path(__file__).resolve().parents[3] / "secrets" / "api_key.hex"
    return key_file.read_text(encoding="utf-8").strip()


class _FreezeEvent:
    """Single freeze/unfreeze audit event (attribute access, like the real one)."""

    def __init__(self, state_name: str, reason: str, operator_id: str) -> None:
        self.state = type("State", (), {"name": state_name})()
        self.reason = reason
        self.operator_id = operator_id
        self.timestamp = time.time_ns() / 1e9


class StubFreezeController:
    """In-memory freeze controller implementing the FreezeController protocol.

    The ANCHORUM site is single-process, so an in-memory implementation gives
    working freeze/unfreeze/audit semantics without the Plane-1 backend.
    """

    class State:
        name = "HEALTHY"

    tenant_id: str = "default"

    def __init__(self) -> None:
        self._state_name = "HEALTHY"
        self._history: list[_FreezeEvent] = []

    @property
    def state(self) -> Any:
        return type("State", (), {"name": self._state_name})()

    @property
    def is_frozen(self) -> bool:
        return self._state_name == "FROZEN"

    @property
    def history(self) -> list[_FreezeEvent]:
        return list(self._history)

    def freeze(self, *, reason: str, operator_id: str, **_kwargs: Any) -> None:
        self._state_name = "FROZEN"
        self._history.append(_FreezeEvent("FROZEN", reason, operator_id))

    def unfreeze(self, *, reason: str, operator_id: str) -> None:
        self._state_name = "UNFROZEN"
        self._history.append(_FreezeEvent("UNFROZEN", reason, operator_id))

    def reset(self, *, reason: str, operator_id: str) -> None:
        self._state_name = "HEALTHY"
        self._history.append(_FreezeEvent("HEALTHY", reason, operator_id))

    def get_status(self) -> str:
        return self._state_name

    def get_audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        return [
            {
                "state": e.state.name,
                "reason": e.reason,
                "operator_id": e.operator_id,
                "timestamp": e.timestamp,
            }
            for e in self._history[-limit:]
        ]


class StubAuthContext:
    """Minimal auth context for the read-only ANCHORUM site."""

    operator_id: str = "anchorum-customer"
    roles: set[str] = {"operator"}


class HtmlAuthRedirectMiddleware(BaseHTTPMiddleware):
    """Redirect unauthenticated browser requests to the login page.

    API requests still receive the normal 401 JSON response.
    """

    PUBLIC_PATHS = {
        "/",
        "/dashboard/login",
        "/favicon.ico",
        "/health",
        "/health/ready",
        "/health/live",
        "/health/nodes",
    }

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path in self.PUBLIC_PATHS or path.startswith("/static/"):
            return await call_next(request)

        # Only intercept browser navigation (HTML requests)
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            api_key = request.headers.get("X-API-Key", "") or request.cookies.get("api_key", "")
            if not api_key:
                return RedirectResponse(url="/dashboard/login", status_code=303)

        return await call_next(request)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> Any:
    logger.info("ANCHORUM plain-HTTP site starting")
    yield
    logger.info("ANCHORUM plain-HTTP site stopping")


def create_app() -> FastAPI:
    repo_root = Path(__file__).resolve().parents[3]
    static_dir = repo_root / "static"

    app = FastAPI(
        title="ANCHORUM Zero-Trust Site",
        version="0.1.0",
        lifespan=_lifespan,
        docs_url=None,
        redoc_url=None,
    )

    auth_context = StubAuthContext()

    # Minimal state required by dashboard router
    app.state.composition_root = type(
        "StubRoot",
        (),
        {
            "freeze_controller": StubFreezeController(),
            "auth_context": auth_context,
            "node_id": "anchorum-http",
        },
    )()

    dashboard_service = DashboardService(
        freeze_controller=app.state.composition_root.freeze_controller,
        auth_context=auth_context,
        node_id="anchorum-http",
    )
    DashboardServiceProvider.set(dashboard_service)

    # Order matters: APIKeyMiddleware is added first so it runs INNERMOST.
    # HtmlAuthRedirectMiddleware wraps it and can intercept 401s for HTML requests.
    app.add_middleware(APIKeyMiddleware)
    app.add_middleware(HtmlAuthRedirectMiddleware)
    app.add_middleware(FreezeGateMiddleware)
    app.mount("/static", StaticFiles(directory=str(static_dir), html=True), name="static")
    app.include_router(dashboard_router)
    app.include_router(anchorum_router)

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/dashboard/anchorum")

    @app.get("/health/ready")
    async def health_ready() -> JSONResponse:
        return JSONResponse({"status": "ready", "plane": "anchorum", "timestamp": time.time_ns() / 1e9})

    @app.post("/api/v1/anchorum/chat")
    async def anchorum_chat(payload: ChatIn) -> Any:
        """Proxy chat to the Egregore Core API (plain HTTP, no WebSocket)."""
        messages: list[dict[str, str]] = []
        if payload.mode == "legal":
            system = _LEGAL_SYSTEM
            if payload.case_id:
                system += "\n\nLive case data:\n" + _case_context(payload.case_id)
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": payload.message})
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(
                    f"{CORE_API_URL}/v1/chat/completions",
                    headers={"X-API-Key": _service_api_key()},
                    json={
                        "model": CHAT_MODEL,
                        "messages": messages,
                        "max_tokens": 1024,
                        "stream": False,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            logger.error("Core API chat failed: %s", exc)
            return JSONResponse(
                status_code=502,
                content={"detail": f"Egregore core unreachable: {exc}"},
            )
        return {
            "ok": True,
            "content": data.get("message", {}).get("content", ""),
            "usage": data.get("usage", {}),
            "governance": data.get("governance", {}),
        }

    return app
