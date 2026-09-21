"""Dashboard router for Plane 2 — real FreezeController wired."""

import logging
import time
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from egregore.governance.permissions import Action, PermissionService
from egregore.http_api.http.middleware.api_key_middleware import is_valid_api_key
from egregore.interface.dashboard.freeze_protocol import FreezeControllerProtocol
from egregore.interface.dashboard.key_health import KeyHealth, KeyHealthStatus
from egregore.interface.dashboard.service import CiHealthReport, CiStatus


class SystemStatus(StrEnum):
    HEALTHY = "HEALTHY"
    FROZEN = "FROZEN"
    UNFROZEN = "UNFROZEN"
    RECONCILING = "RECONCILING"


logger = logging.getLogger("egregore.dashboard")

router = APIRouter(prefix="/dashboard")

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.globals["SystemStatus"] = SystemStatus
templates.env.globals["KeyHealth"] = KeyHealthStatus


def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "N/A"
    dt = datetime.fromtimestamp(ts, tz=UTC)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _event_to_dict(event):
    """Convert real FreezeEvent to template-friendly dict."""
    ts = event.timestamp_ns / 1e9 if event.timestamp_ns else None
    op = event.operator_id
    if not op and event.context:
        op = event.context.get("operator_id")
    return {
        "timestamp": ts,
        "operator": op or "UNKNOWN",
        "action": event.state.name if event.state else "UNKNOWN",
        "reason": event.reason or "",
        "new_state": event.state.name if event.state else "UNKNOWN",
        "event_id": (
            f"evt-{event.timestamp_ns}" if event.timestamp_ns else "evt-unknown"
        ),
    }


def _get_freeze_controller(request: Request) -> FreezeControllerProtocol:
    """Get the real FreezeController from the app state."""
    return request.app.state.composition_root.freeze_controller


def _freeze_status_to_system_status(fc: FreezeControllerProtocol) -> SystemStatus:
    """Map the FreezeController's FreezeState to our display enum."""
    state = fc.state  # FreezeState enum (HEALTHY, FROZEN, RECONCILING)
    # Direct name match — FreezeState members match SystemStatus members
    return SystemStatus.HEALTHY if state.name == "HEALTHY" else SystemStatus[state.name]


def _require_admin(request: Request):
    identity = getattr(request.state, "user", None)
    PermissionService().require(identity, Action.SYSTEM_FREEZE)


# ---------- Auth pages ----------
@router.get("/login")
async def login_page(request: Request, error: str = ""):
    """Self-hosted login page: enter API key to receive a session cookie."""
    return templates.TemplateResponse(request, "login.html", {"error": error})


@router.post("/login")
async def login_submit(request: Request, api_key: str = Form(...)):
    """Validate API key and set session cookie."""
    if not is_valid_api_key(api_key.strip()):
        return RedirectResponse(url="/dashboard/login?error=invalid", status_code=303)

    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key="api_key",
        value=api_key.strip(),
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/logout")
async def logout(request: Request):
    """Clear the API key cookie and return to the login page."""
    response = RedirectResponse(url="/dashboard/login", status_code=303)
    response.delete_cookie(key="api_key", path="/")
    return response


# Static ANCHORUM site entry point (protected by the api_key cookie).
ANCHORUM_INDEX = (
    Path(__file__).resolve().parents[4] / "static" / "anchorum" / "index.html"
)


@router.get("/anchorum")
async def anchorum_page(request: Request):
    if not ANCHORUM_INDEX.exists():
        raise HTTPException(status_code=404, detail="ANCHORUM page not found")
    return FileResponse(str(ANCHORUM_INDEX))


# ---------- Pages ----------
@router.get("")
async def dashboard_index(request: Request):
    """Main dashboard page."""
    fc = _get_freeze_controller(request)
    status = _freeze_status_to_system_status(fc)
    timestamp = time.time_ns() / 1e9
    health = KeyHealth(has_key=True, key_length=64, permissions="600")
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "status": status,
            "timestamp": timestamp,
            "health": health,
            "fmt_ts": _fmt_ts,
        },
    )


@router.get("/status")
async def status_card(request: Request):
    """HTMX fragment: status card."""
    fc = _get_freeze_controller(request)
    status = _freeze_status_to_system_status(fc)
    timestamp = time.time_ns() / 1e9
    return templates.TemplateResponse(
        request,
        "fragments/status_card.html",
        {
            "status": status,
            "timestamp": timestamp,
            "fmt_ts": _fmt_ts,
        },
    )


@router.get("/controls")
async def freeze_controls(request: Request):
    """HTMX fragment: freeze/unfreeze buttons."""
    fc = _get_freeze_controller(request)
    status = _freeze_status_to_system_status(fc)
    return templates.TemplateResponse(
        request,
        "fragments/freeze_controls.html",
        {
            "status": status,
        },
    )


@router.post("/freeze")
async def freeze_action(
    request: Request, reason: str = Form(...), operator: str = Form("operator")
):
    """Freeze the system."""
    _require_admin(request)
    fc = _get_freeze_controller(request)
    fc.freeze(reason=reason, operator_id=operator)
    # Return updated controls + status fragments
    status = _freeze_status_to_system_status(fc)
    timestamp = time.time_ns() / 1e9
    status_html = templates.get_template("fragments/status_card.html").render(
        {"status": status, "timestamp": timestamp, "fmt_ts": _fmt_ts}
    )
    controls_html = templates.get_template("fragments/freeze_controls.html").render(
        {"status": status}
    )
    return status_html + controls_html


@router.post("/unfreeze")
async def unfreeze_action(
    request: Request, reason: str = Form(...), operator: str = Form("operator")
):
    """Unfreeze the system."""
    _require_admin(request)
    fc = _get_freeze_controller(request)
    fc.unfreeze(reason=reason, operator_id=operator)
    status = _freeze_status_to_system_status(fc)
    timestamp = time.time_ns() / 1e9
    status_html = templates.get_template("fragments/status_card.html").render(
        {"status": status, "timestamp": timestamp, "fmt_ts": _fmt_ts}
    )
    controls_html = templates.get_template("fragments/freeze_controls.html").render(
        {"status": status}
    )
    return status_html + controls_html


@router.post("/reset")
async def reset_action(
    request: Request, reason: str = Form(...), operator: str = Form("operator")
):
    """Reset the system to HEALTHY."""
    _require_admin(request)
    fc = _get_freeze_controller(request)
    fc.reset(reason=reason, operator_id=operator)
    status = _freeze_status_to_system_status(fc)
    timestamp = time.time_ns() / 1e9
    status_html = templates.get_template("fragments/status_card.html").render(
        {"status": status, "timestamp": timestamp, "fmt_ts": _fmt_ts}
    )
    controls_html = templates.get_template("fragments/freeze_controls.html").render(
        {"status": status}
    )
    return status_html + controls_html


@router.get("/audit")
async def audit_log(request: Request):
    """HTMX fragment: audit log list."""
    fc = _get_freeze_controller(request)
    events = [_event_to_dict(e) for e in fc.history]
    return templates.TemplateResponse(
        request,
        "fragments/audit_log.html",
        {
            "events": events,
            "fmt_ts": _fmt_ts,
        },
    )


@router.get("/key-health")
async def key_health(request: Request):
    """HTMX fragment: key health indicators."""
    health = KeyHealth(has_key=True, key_length=64, permissions="600")
    return templates.TemplateResponse(
        request,
        "fragments/key_health.html",
        {
            "health": health,
        },
    )


# ---------- Service stubs ----------
class DashboardService:
    def __init__(
        self,
        freeze_controller: FreezeControllerProtocol,
        auth_context=None,
        node_id=None,
    ):
        self.freeze_controller = freeze_controller
        self.auth_context = auth_context
        self.node_id = node_id


class DashboardServiceProvider:
    _instance = None

    @classmethod
    def set(cls, service):
        cls._instance = service

    @classmethod
    def get(cls):
        return cls._instance


@router.get("/ci-health")
async def ci_health(request: Request):
    return templates.TemplateResponse(
        request,
        "fragments/ci_health.html",
        {
            "ci": CiHealthReport(
                status=CiStatus.UNKNOWN,
                last_run=None,
                lint_ok=False,
                type_ok=False,
                security_ok=False,
                summary="No CI data",
            ),
            "CiStatus": CiStatus,
            "fmt_ts": _fmt_ts,
        },
    )


@router.get("/debug/state")
async def debug_state(request: Request):
    fc = _get_freeze_controller(request)
    return {
        "state": fc.state.name,
        "is_frozen": fc.is_frozen,
        "history_count": len(fc.history),
        "last_event": fc.history[-1].state.name if fc.history else None,
    }
