"""Tests for the frontend-compatibility router.

Run from repo root:
    pytest tests/test_compat_router.py -v
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from egregore.interface import compat_router as cr


@pytest.fixture
def client():
    """TestClient with the auth dependency overridden.

    The override is registered on the FastAPI app instance, not via
    monkeypatch, because FastAPI stores the dependency callable in the
    route's `dependant` at decoration time and consults
    `app.dependency_overrides` before calling it.
    """
    app = FastAPI()
    app.include_router(cr.router)
    app.dependency_overrides[cr.require_api_key] = lambda: "test-key"
    return TestClient(app)


def _proc(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_enabled_from_is_enabled(monkeypatch):
    calls = []

    def fake(args):
        calls.append(args)
        if args[0] == "is-active":
            return _proc(stdout="active")
        if args[0] == "is-enabled":
            return _proc(stdout="disabled")
        return _proc()

    monkeypatch.setattr(cr, "_run_systemctl", fake)
    monkeypatch.setattr(cr, "SERVICES", ["svc-a"])
    assert cr._service_snapshot() == [
        {"name": "svc-a", "status": "active", "enabled": "disabled"}
    ]
    assert ["is-active", "svc-a"] in calls
    assert ["is-enabled", "svc-a"] in calls


def test_failed_state_preserved(monkeypatch):
    def fake(args):
        if args[0] == "is-active":
            return _proc(stdout="failed", returncode=3)
        if args[0] == "is-enabled":
            return _proc(stdout="enabled")
        return _proc()

    monkeypatch.setattr(cr, "_run_systemctl", fake)
    monkeypatch.setattr(cr, "SERVICES", ["svc-a"])
    assert cr._service_snapshot()[0]["status"] == "failed"


def test_unknown_service_404(client):
    assert client.post("/api/services/nonexistent/start").status_code == 404


def test_invalid_action_400(client, monkeypatch):
    monkeypatch.setattr(cr, "SERVICES", ["svc-a"])
    assert client.post("/api/services/svc-a/nuke").status_code == 400


def test_status_uses_is_active(client, monkeypatch):
    seen = []

    def fake(args):
        seen.append(args)
        return _proc(stdout="active")

    monkeypatch.setattr(cr, "_run_systemctl", fake)
    monkeypatch.setattr(cr, "SERVICES", ["svc-a"])
    r = client.post("/api/services/svc-a/status")
    assert r.status_code == 200
    assert r.json() == {"status": "active"}
    assert seen and seen[0][0] == "is-active"


def test_logs_returns_output(client, monkeypatch):
    monkeypatch.setattr(cr, "SERVICES", ["svc-a"])
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _proc(stdout="line1\nline2\n"),
    )
    r = client.post("/api/services/svc-a/logs")
    assert r.status_code == 200
    assert "line1" in r.json()["logs"]


def test_logs_timeout_504(client, monkeypatch):
    monkeypatch.setattr(cr, "SERVICES", ["svc-a"])

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="journalctl", timeout=10)

    monkeypatch.setattr(subprocess, "run", boom)
    assert client.post("/api/services/svc-a/logs").status_code == 504


def test_start_failure_500(client, monkeypatch):
    monkeypatch.setattr(cr, "SERVICES", ["svc-a"])
    monkeypatch.setattr(
        cr, "_run_systemctl",
        lambda a: _proc(stderr="permission denied", returncode=1),
    )
    r = client.post("/api/services/svc-a/start")
    assert r.status_code == 500
    assert "permission denied" in r.json()["detail"]


def test_dashboard_survives_exception(client, monkeypatch):
    def fake(args):
        if args[-1] == "bad-svc":
            raise RuntimeError("boom")
        return _proc(stdout="active")

    monkeypatch.setattr(cr, "_run_systemctl", fake)
    monkeypatch.setattr(cr, "SERVICES", ["good-svc", "bad-svc"])
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    by = {s["name"]: s for s in r.json()["services"]}
    assert by["good-svc"]["status"] == "active"
    assert by["bad-svc"]["status"] == "unknown"


def test_dependency_override_bypasses_auth(client, monkeypatch):
    """Regression guard: if this fails, the override mechanism is wrong
    and every other route test above is invalid.

    SERVICES is emptied and _run_systemctl is monkeypatched so this test
    is purely about the override plumbing, with no subprocess calls.
    """
    monkeypatch.setattr(cr, "SERVICES", [])
    monkeypatch.setattr(cr, "_run_systemctl", lambda a: _proc(stdout="active"))
    assert client.get("/api/dashboard").status_code == 200
