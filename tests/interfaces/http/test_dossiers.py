from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

from fastapi.testclient import TestClient  # type: ignore[import-not-found]

from egregore.http_api.http.app import create_app
from egregore.http_api.http.middleware import api_key_middleware

VALID_KEY = "a" * 64
api_key_middleware._API_KEYS = {VALID_KEY: ("default", "user", "admin")}


def test_generate_endpoint_smoke() -> None:
    app = create_app()
    client = TestClient(app)

    payload = {
        "organization_id": "org_1",
        "case_id": "case_1",
        "actor_id": "actor_api_key_1",
        "input_fingerprint": "fp_1",
        "engine_version": "engine_vA",
        "policy_version": "policy_v1",
        "input_payload": {"raw": "messy legal notes"},
        "causality_id": "cmd-1",
        # timestamp_ns intentionally omitted -> service derives deterministically
        # request_id optional
    }

    resp = client.post(
        "/v1/dossiers/generate", json=payload, headers={"X-API-Key": VALID_KEY}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert "data" in body
