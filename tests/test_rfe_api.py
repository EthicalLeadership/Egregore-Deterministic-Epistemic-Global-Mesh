"""HTTP API tests for the RFE router."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from egregore.http_api.http.app import create_app

# Use the same key registry the middleware validates against.
from egregore.http_api.http.middleware.api_key_middleware import _API_KEYS
from tests.redteam.conftest import sign_stream

_API_KEY_ENTRY = next(iter(_API_KEYS.items()), None)
API_KEY = _API_KEY_ENTRY[0] if _API_KEY_ENTRY else "a" * 64


pytestmark = [pytest.mark.redteam]


@pytest.fixture
def client() -> TestClient:
    app = create_app(build_container=False)
    return TestClient(app)


@pytest.fixture
def valid_manifest(signing_key: str) -> dict[str, Any]:
    return {
        "case_id": "case_api_001",
        "timestamp": "2026-06-29T00:00:00+00:00",
        "streams": [
            sign_stream(
                {
                    "stream_id": "api_s1",
                    "type": "court_ruling",
                    "source_tier": 1,
                    "content": {
                        "claim": "positive",
                        "subject": "liability",
                        "text": "Liability found.",
                    },
                    "confidence": 0.95,
                    "provenance_hash": "api_h1",
                    "signature": None,
                    "timestamp": "2026-06-28T12:00:00+00:00",
                    "decay": {"method": "unbounded"},
                    "severity_impact": 0.9,
                    "relevance_tags": ["liability"],
                },
                signing_key,
            ),
        ],
        "constraints": {"output_format": "pdf-a-1b", "language": "en"},
    }


def test_generate_endpoint(client: TestClient, valid_manifest: dict[str, Any]) -> None:
    response = client.post(
        "/api/v1/rfe/generate", json=valid_manifest, headers={"X-API-Key": API_KEY}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["report_hash"]
    assert data["decision_log_hash"]
    assert data["report"]["case_id"] == "case_api_001"


def test_feedback_endpoint(client: TestClient) -> None:
    response = client.post(
        "/api/v1/rfe/feedback",
        json={
            "case_id": "case_api_001",
            "content": {"text": "New eyewitness account."},
            "source_tier": 5,
            "confidence": 0.6,
        },
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["stream"]["type"] == "human_feedback"


def test_config_endpoint(client: TestClient) -> None:
    response = client.get("/api/v1/rfe/config", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "scoring_weights" in data["config"]


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/api/v1/rfe/health", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_future_timestamp_rejected_by_api(client: TestClient, signing_key: str) -> None:
    manifest = {
        "case_id": "case_api_future",
        "timestamp": "2026-06-29T00:00:00+00:00",
        "streams": [
            sign_stream(
                {
                    "stream_id": "api_future",
                    "type": "testimony",
                    "source_tier": 2,
                    "content": {"claim": "positive", "subject": "x", "text": "Future."},
                    "confidence": 0.9,
                    "provenance_hash": "hf",
                    "signature": None,
                    "timestamp": "2026-06-29T02:00:00+00:00",
                    "decay": {"method": "unbounded"},
                    "severity_impact": 0.8,
                    "relevance_tags": [],
                },
                signing_key,
            ),
        ],
        "constraints": {"output_format": "pdf-a-1b", "language": "en"},
    }
    response = client.post(
        "/api/v1/rfe/generate", json=manifest, headers={"X-API-Key": API_KEY}
    )
    assert response.status_code == 422
