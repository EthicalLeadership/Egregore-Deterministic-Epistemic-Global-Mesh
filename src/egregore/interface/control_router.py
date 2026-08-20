"""SystemD service control API for Egregore services."""

import subprocess
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1/services", tags=["services"])

SERVICES = [
    "egregore-anchorum-http",
    "egregore-bootstrap",
    "egregore-control-center",
    "egregore-core-api",
    "egregore-ems-proxy",
    "egregore-federation-watcher",
    "egregore-gateway",
]


def _run_systemctl(args: list[str]) -> subprocess.CompletedProcess:
    """Execute systemctl command."""
    return subprocess.run(
        ["systemctl", "--user"] + args,
        capture_output=True,
        text=True,
        check=False,
    )


@router.get("/")
async def list_services() -> list[dict[str, str]]:
    """List all Egregore services with their status."""
    result = []
    for name in SERVICES:
        proc = _run_systemctl(["is-active", name])
        status = (
            proc.stdout.strip()
            if proc.returncode == 0
            else (proc.stderr.strip() or "inactive")
        )
        enabled_proc = _run_systemctl(["is-enabled", name])
        enabled = (
            enabled_proc.stdout.strip()
            if enabled_proc.returncode == 0
            else "disabled"
        )
        result.append(
            {
                "name": name,
                "status": status,
                "enabled": enabled,
            }
        )
    return result


@router.post("/{name}/{action}")
async def service_action(name: str, action: str) -> dict[str, Any]:
    """Execute an action on a service (start, stop, restart, status, logs, enable, disable)."""
    if name not in SERVICES:
        raise HTTPException(404, f"Unknown service: {name}")
    if action not in ["start", "stop", "restart", "status", "logs", "enable", "disable"]:
        raise HTTPException(400, "Invalid action")

    if action == "status":
        proc = _run_systemctl(["is-active", name])
        return {
            "status": (
                proc.stdout.strip()
                if proc.returncode == 0
                else (proc.stderr.strip() or "inactive")
            )
        }

    if action == "logs":
        log_proc = subprocess.run(
            ["journalctl", "--user", "-u", name, "-n", "200", "--no-pager"],
            capture_output=True,
            text=True,
            check=False,
        )
        return {
            "logs": log_proc.stdout if log_proc.returncode == 0 else log_proc.stderr,
        }

    proc = _run_systemctl([action, name])
    if proc.returncode != 0:
        raise HTTPException(500, proc.stderr or proc.stdout)
    return {"status": "ok", "action": action, "output": proc.stdout}
