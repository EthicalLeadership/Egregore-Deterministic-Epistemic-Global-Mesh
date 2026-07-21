from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

from fastapi.testclient import TestClient  # type: ignore[import-not-found]

from egregore.http_api.http.app import create_app
from egregore.http_api.http.middleware import api_key_middleware

VALID_KEY = "a" * 64
api_key_middleware._API_KEYS = {VALID_KEY: ("default", "user", "admin")}


def test_intake_upload_txt_file() -> None:
    """Smoke test: upload a plain-text file through the intake endpoint."""
    app = create_app()
    client = TestClient(app)

    files = {
        "documents": (
            "test_sop.txt",
            b"Standard Operating Procedure for cannabis packaging.",
            "text/plain",
        ),
    }
    data = {
        "organization_id": "org_test",
        "case_id": "case_test_1",
        "actor_id": "actor_test",
        "causality_id": "cause_test_1",
        "vertical": "cannabis",
    }

    resp = client.post(
        "/v1/intake/upload", data=data, files=files, headers={"X-API-Key": VALID_KEY}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["files_processed"] == 1
    assert "intake_id" in body
    assert len(body["results"]) == 1
    assert body["results"][0]["file"] == "test_sop.txt"
    assert "fingerprint" in body["results"][0]


def test_intake_upload_multiple_files() -> None:
    app = create_app()
    client = TestClient(app)

    files = [
        ("documents", ("a.txt", b"Content A", "text/plain")),
        ("documents", ("b.txt", b"Content B", "text/plain")),
    ]
    data = {
        "organization_id": "org_multi",
        "case_id": "case_multi",
        "actor_id": "actor_multi",
        "causality_id": "cause_multi",
        "vertical": "cannabis",
    }

    resp = client.post(
        "/v1/intake/upload", data=data, files=files, headers={"X-API-Key": VALID_KEY}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["files_processed"] == 2


def test_intake_upload_missing_required_field() -> None:
    app = create_app()
    client = TestClient(app)

    files = {"documents": ("x.txt", b"x", "text/plain")}
    data = {
        # organization_id intentionally missing
        "case_id": "case_bad",
        "actor_id": "actor_bad",
        "causality_id": "cause_bad",
        "vertical": "cannabis",
    }

    resp = client.post(
        "/v1/intake/upload", data=data, files=files, headers={"X-API-Key": VALID_KEY}
    )
    assert (
        resp.status_code == 422
    )  # FastAPI validation error for missing required field


def test_intake_upload_no_documents() -> None:
    app = create_app()
    client = TestClient(app)

    data = {
        "organization_id": "org_none",
        "case_id": "case_none",
        "actor_id": "actor_none",
        "causality_id": "cause_none",
        "vertical": "cannabis",
    }

    resp = client.post("/v1/intake/upload", data=data, headers={"X-API-Key": VALID_KEY})
    # FastAPI validates File(...) at the framework level => 422
    assert resp.status_code == 422, resp.text
    assert "detail" in resp.text
