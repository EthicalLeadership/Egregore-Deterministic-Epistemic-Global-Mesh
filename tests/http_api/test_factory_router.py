"""Tests for /api/v1/factory endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from egregore.http_api.http.app import create_app

VALID_KEY = "a" * 64


class FakeInferenceHost:
    """Lightweight EgregoreInferenceHost replacement that never touches disk or backends."""

    def __init__(
        self, content: str = "OK", tokens: int = 5, model_specs: dict[str, Any] | None = None
    ) -> None:
        self.content = content
        self.tokens = tokens
        self.model_specs = model_specs or {}
        self.inference_service = None

    def execute(
        self,
        model_id: str,
        prompt: str,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> tuple[str, int, str]:
        return self.content, self.tokens, "fake"

    def health(self) -> dict[str, Any]:
        return {
            "egregore_inference_available": True,
            "configured_models": {
                mid: {"model_id": spec.get("model_id") or spec.get("path") or mid}
                for mid, spec in self.model_specs.items()
            },
            "service": {"available": True, "backends": {}},
        }


@pytest.fixture
def factory_client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch):
    from egregore.http_api.http.middleware import api_key_middleware

    # These tests exercise the pipeline, not the QC gate (tested separately).
    monkeypatch.setenv("EGREGORE_FACTORY_QC", "off")
    # Keep test telemetry out of the production report dir.
    monkeypatch.setenv("EGREGORE_FACTORY_TELEMETRY_DIR", str(tmp_path))
    from egregore.factory import telemetry

    telemetry.reset_recorder()
    api_key_middleware._API_KEYS = {VALID_KEY: ("default", "user", "admin")}

    app = create_app(build_container=False)

    # Pre-seed the model host so factory runs never hit disk.
    profiles_path = (
        Path(__file__).resolve().parents[2] / "config" / "factory_profiles.yaml"
    )
    with open(profiles_path, encoding="utf-8") as f:
        profiles = yaml.safe_load(f)

    app.state.factory_model_host = FakeInferenceHost(
        content="factory-output",
        tokens=7,
        model_specs=profiles.get("models", {}),
    )

    return TestClient(app)


def test_list_factory_modes(factory_client: TestClient):
    r = factory_client.get("/api/v1/factory/modes", headers={"X-API-Key": VALID_KEY})
    assert r.status_code == 200
    data = r.json()
    assert "coding_factory" in data["modes"]
    assert "general_assistant" in data["modes"]
    assert data["default_mode"] == "coding_factory"


def test_factory_health(factory_client: TestClient):
    r = factory_client.get("/api/v1/factory/health", headers={"X-API-Key": VALID_KEY})
    assert r.status_code == 200
    data = r.json()
    assert "configured_models" in data
    assert set(data["configured_models"].keys()) == {
        "qwen_1.5b",
        "qwen_7b",
        "deepseek_coder_6.7b",
    }
    for spec in data["configured_models"].values():
        assert "model_id" in spec
        assert spec["model_id"] == "my-coder-ft"


def test_factory_mode_health(factory_client: TestClient):
    r = factory_client.get(
        "/api/v1/factory/coding_factory/health",
        headers={"X-API-Key": VALID_KEY},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "coding_factory"
    assert "models" in data
    assert "ready" in data


def test_factory_mode_health_unknown_mode(factory_client: TestClient):
    r = factory_client.get(
        "/api/v1/factory/no_such_mode/health",
        headers={"X-API-Key": VALID_KEY},
    )
    assert r.status_code == 404


def test_run_factory_default(factory_client: TestClient):
    r = factory_client.post(
        "/api/v1/factory",
        json={"input": "write a hello world function"},
        headers={"X-API-Key": VALID_KEY},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "coding_factory"
    assert data["pipeline_version"] == 2
    assert "stations" in data
    assert set(data["stations"].keys()) == {
        "spec_synthesis",
        "scaffolding",
        "cnc",
        "static_analysis",
        "dynamic_test",
        "moral_compliance",
        "final_qc",
    }
    assert (
        data["provenance"]["qc_verdict"] == "FAIL"
    )  # FakeLlm returns "factory-output", no PASS
    assert data["final_output"].startswith("[QC FLAGGED]")


def test_run_factory_explicit_mode(factory_client: TestClient):
    r = factory_client.post(
        "/api/v1/factory/general_assistant",
        json={"input": "explain recursion", "max_tokens": 100, "temperature": 0.5},
        headers={"X-API-Key": VALID_KEY},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "general_assistant"
    assert data["provenance"]["total_tokens"] > 0


def test_run_factory_unknown_mode(factory_client: TestClient):
    r = factory_client.post(
        "/api/v1/factory/no_such_mode",
        json={"input": "hello"},
        headers={"X-API-Key": VALID_KEY},
    )
    assert r.status_code == 404
    assert "no_such_mode" in r.json()["detail"]


def test_run_factory_invalid_input_rejected(factory_client: TestClient):
    r = factory_client.post(
        "/api/v1/factory",
        json={"input": ""},
        headers={"X-API-Key": VALID_KEY},
    )
    assert r.status_code == 422
