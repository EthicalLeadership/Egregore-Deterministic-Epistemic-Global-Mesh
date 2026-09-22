"""Compatibility routes for the /static/fpi React frontend.

The frontend (built from commit 2300041) calls:
    GET  /api/dashboard
    POST /api/services/{name}/{action}

The backend exposes them under different paths:
    GET  /api/v1/services/
    POST /api/v1/services/{name}/{action}

This module exposes the exact paths the frontend expects.

Design notes:
  * Handlers are `def`, not `async def`. FastAPI runs sync handlers in a
    threadpool, so blocking systemctl subprocess calls do not stall the
    event loop.
  * `systemctl is-active` writes the state to stdout even on non-zero
    exit (e.g. `failed`). We prefer stdout, then stderr, then a fallback.
  * `enabled` is queried separately from `is-enabled`, not aliased to
    `is-active`.
  * /api/dashboard metrics come from /proc. Fields with no source are
    null, not zero, so the UI can distinguish "not measured" from
    "measured as zero". `source` records provenance.
  * Both routes require a valid API key (header or cookie) via
    `require_api_key`.
"""

from __future__ import annotations

import subprocess
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from egregore.interface.auth_dep import require_api_key
from egregore.interface.control_router import SERVICES, _run_systemctl

router = APIRouter(prefix="/api", tags=["frontend-compat"])

_LOG_TIMEOUT_S = 10


def _stdout(proc: Any) -> str:
    return (getattr(proc, "stdout", "") or "").strip()


def _stderr(proc: Any) -> str:
    return (getattr(proc, "stderr", "") or "").strip()


def _active_state(name: str) -> str:
    """Return the is-active state ('active', 'inactive', 'failed', ...).

    systemctl is-active prints the state on stdout even when exiting
    non-zero, so prefer stdout, then stderr, then a fallback.
    """
    proc = _run_systemctl(["is-active", name])
    return _stdout(proc) or _stderr(proc) or "inactive"


def _enabled_state(name: str) -> str:
    """Return the is-enabled state ('enabled', 'disabled', 'masked', ...)."""
    proc = _run_systemctl(["is-enabled", name])
    return _stdout(proc) or _stderr(proc) or "disabled"


def _service_snapshot() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for name in SERVICES:
        try:
            out.append(
                {
                    "name": name,
                    "status": _active_state(name),
                    "enabled": _enabled_state(name),
                }
            )
        except Exception:
            out.append({"name": name, "status": "unknown", "enabled": "unknown"})
    return out


def _read_uptime() -> int:
    """Seconds since boot from /proc/uptime. Returns 0 if unreadable."""
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return 0


def _read_cpu_percent(sample_s: float = 0.05) -> float:
    """CPU utilisation by diffing /proc/stat over a short window.

    Blocks for approximately `sample_s` seconds. FastAPI runs sync
    handlers in a threadpool, so a handful of concurrent 50 ms samples
    is fine. If poller count grows, cache this value or sample in the
    background.

    Returns 0.0 if /proc/stat is unreadable or malformed.
    """
    import time

    def _snap() -> tuple[int, int]:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        idle = int(parts[4])
        total = sum(int(x) for x in parts[1:])
        return idle, total

    try:
        i1, t1 = _snap()
        time.sleep(sample_s)
        i2, t2 = _snap()
    except (OSError, ValueError, IndexError):
        return 0.0

    dt = t2 - t1
    if dt <= 0:
        return 0.0
    return round(100.0 * (1.0 - (i2 - i1) / dt), 1)


def _read_memory() -> tuple[float, int, int]:
    """Return (percent_used, used_mb, total_mb) from /proc/meminfo.

    Returns (0.0, 0, 0) if /proc/meminfo is unreadable or malformed.
    """
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, rest = line.partition(":")
                info[k.strip()] = int(rest.strip().split()[0])
        total_kb = info.get("MemTotal", 0)
        avail_kb = info.get("MemAvailable", info.get("MemFree", 0))
        used_kb = max(0, total_kb - avail_kb)
        pct = round(100.0 * used_kb / total_kb, 1) if total_kb else 0.0
        return pct, used_kb // 1024, total_kb // 1024
    except (OSError, ValueError, IndexError):
        return 0.0, 0, 0


def _real_metrics() -> dict:
    """Live metrics from /proc.

    Fields with no source are null, not zero, so the UI can distinguish
    "not measured" from "measured as zero". `source` records provenance.
    """
    cpu = _read_cpu_percent()
    mem_pct, mem_used, mem_total = _read_memory()
    return {
        "source": "proc",
        "nodes": {"total": 1, "active": 1, "offline": 0},
        "jobs": {
            "queued": None, "assigned": None, "running": None,
            "completed": None, "failed": None, "total": None,
        },
        "compute": {
            "cpu_percent": cpu,
            "memory_percent": mem_pct,
            "memory_used_mb": mem_used,
            "memory_total_mb": mem_total,
            "gpu_percent": None,
            "gpu_memory_percent": None,
            "gpu_memory_used_mb": None,
            "gpu_memory_total_mb": None,
        },
        "inference": {
            "active_models": None, "tokens_per_sec": None,
            "requests_per_min": None, "avg_latency_ms": None,
        },
        "power": {"gpu_watts": None, "system_watts": None, "tdp_percent": None},
        "network": {
            "inter_node_rx_mbps": None, "inter_node_tx_mbps": None,
            "internet_rx_mbps": None, "internet_tx_mbps": None,
        },
        "uptime_seconds": _read_uptime(),
    }


@router.get("/dashboard")
def dashboard(_api_key: str = Depends(require_api_key)) -> dict[str, Any]:
    """Frontend dashboard snapshot."""
    services = _service_snapshot()
    health = "ok" if all(s["status"] == "active" for s in services) else "degraded"
    return {
        "services": services,
        "metrics": _real_metrics(),
        "health": {"status": health},
    }


@router.post("/services/{name}/{action}")
def service_action_compat(
    name: str,
    action: str,
    _api_key: str = Depends(require_api_key),
) -> dict[str, Any]:
    """Frontend-compat service control."""
    if name not in SERVICES:
        raise HTTPException(404, f"Unknown service: {name}")

    allowed = ("start", "stop", "restart", "status", "logs", "enable", "disable")
    if action not in allowed:
        raise HTTPException(400, f"Invalid action: {action!r}")

    if action == "status":
        return {"status": _active_state(name)}

    if action == "logs":
        try:
            log_proc = subprocess.run(
                ["journalctl", "--user", "-u", name, "-n", "200", "--no-pager"],
                capture_output=True,
                text=True,
                check=False,
                timeout=_LOG_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(504, f"journalctl timed out after {_LOG_TIMEOUT_S}s")
        except FileNotFoundError:
            raise HTTPException(503, "journalctl not available on this host")

        if log_proc.returncode != 0 and not log_proc.stdout:
            raise HTTPException(502, _stderr(log_proc) or "journalctl failed")

        return {"logs": _stdout(log_proc) or _stderr(log_proc)}

    try:
        proc = _run_systemctl([action, name])
    except FileNotFoundError:
        raise HTTPException(503, "systemctl not available on this host")

    if proc.returncode != 0:
        msg = _stderr(proc) or _stdout(proc) or f"systemctl {action} {name} failed"
        raise HTTPException(500, msg)

    return {"status": "ok", "action": action, "output": _stdout(proc)}
