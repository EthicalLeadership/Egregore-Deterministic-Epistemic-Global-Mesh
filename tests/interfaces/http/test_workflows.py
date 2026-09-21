from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

from fastapi.testclient import TestClient  # type: ignore[import-not-found]

from egregore.http_api.http.app import create_app
from egregore.http_api.http.middleware import api_key_middleware

VALID_KEY = "a" * 64
api_key_middleware._API_KEYS = {VALID_KEY: ("default", "user", "admin")}


def test_workflow_test_health_smoke() -> None:
    app = create_app()
    client = TestClient(app)

    payload = {
        "input": {"ping": "pong"},
        "idempotency_key": "idem-test-health-1",
        "correlation_id": "corr-test-health-1",
    }

    resp = client.post(
        "/workflows/test-health", json=payload, headers={"X-API-Key": VALID_KEY}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "id" in body
    assert "status" in body
    assert body["status"] in {"running", "completed", "failed"}


def test_workflow_test_health_get_status() -> None:
    app = create_app()
    client = TestClient(app)

    payload = {
        "input": {"ping": "pong"},
        "idempotency_key": "idem-test-health-2",
        "correlation_id": "corr-test-health-2",
    }

    resp = client.post(
        "/workflows/test-health", json=payload, headers={"X-API-Key": VALID_KEY}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    workflow_id = body["id"]

    # In this MVP implementation execution is synchronous,
    # so status should already be stable.
    resp2 = client.get(f"/workflows/{workflow_id}", headers={"X-API-Key": VALID_KEY})
    assert resp2.status_code == 200, resp2.text
    body2 = resp2.json()
    assert body2["id"] == workflow_id
    assert "status" in body2
    assert body2["status"] in {"running", "completed", "failed", "unknown"}
