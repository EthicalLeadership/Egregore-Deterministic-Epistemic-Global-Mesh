"""Tests for the cell execution layer and Ombudsman Router v2."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from egregore.cells.executor import CellExecutor, CellResult
from egregore.cells.model_host import ModelHost
from egregore.cells.models import CellSpec
from egregore.cells.registry import CellRegistry
from egregore.cells.rfe_adapter import build_manifest, cell_result_to_stream
from egregore.interface.ombudsman_router import router as ombudsman_router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def temp_cells_dir(tmp_path: Path) -> Path:
    """Create a temporary cells directory with one minimal cell spec."""
    cells_dir = tmp_path / "cells"
    cell_dir = cells_dir / "demo_cell"
    cell_dir.mkdir(parents=True)
    artifacts = cell_dir / "artifacts"
    artifacts.mkdir()

    for stage in ("draw", "layout", "erect", "build", "finish", "inspect", "deliver"):
        (artifacts / f"{stage}.json").write_text(json.dumps({"stage": stage}))

    stage_gates = {
        "plan": str(spec_path := cell_dir / "spec.yaml"),
        "draw": str(artifacts / "draw.json"),
        "layout": str(artifacts / "layout.json"),
        "erect": str(artifacts / "erect.json"),
        "build": str(artifacts / "build.json"),
        "finish": str(artifacts / "finish.json"),
        "inspect": str(artifacts / "inspect.json"),
        "deliver": str(artifacts / "deliver.json"),
    }

    spec = {
        "cell_id": "demo_cell",
        "version": "1.0.0",
        "taxonomy": {
            "root": "university",
            "branch": "demo",
            "leaf": "minimal",
        },
        "owner": "test",
        "type": "university",
        "tier": 3,
        "max_load": 0.9,
        "purpose": "Minimal demo cell for tests.",
        "inputs": [{"name": "input", "type": "string", "required": True}],
        "outputs": [{"name": "result", "type": "string"}],
        "output_format": {
            "stream_type": "demo",
            "claim_field": "verdict",
            "claim_map": {"PASS": "positive"},
        },
        "pipeline": {
            "stages": [
                {
                    "stage_id": "extract",
                    "name": "Extract",
                    "model": "fake_model",
                    "system": "You extract.",
                    "prompt": "Extract from: {input}",
                    "output_format": "json",
                    "max_tokens": 64,
                    "temperature": 0.0,
                },
                {
                    "stage_id": "answer",
                    "name": "Answer",
                    "model": "fake_model",
                    "system": "You answer.",
                    "prompt": "Answer based on: {extract_output}",
                    "output_format": "json",
                    "max_tokens": 64,
                    "temperature": 0.0,
                    "depends_on": ["extract"],
                },
                {
                    "stage_id": "verify",
                    "name": "Verify",
                    "tool": "noop_verify",
                    "depends_on": ["answer"],
                },
            ]
        },
        "models": [
            {"model_id": "fake_model", "purpose": "Fake", "path": "/dev/null/fake.gguf"}
        ],
        "verification": {"rules": []},
        "moral_compliance": {"egregore_laws": []},
        "dependencies": [],
        "artifacts": {"stage_gates": stage_gates},
    }

    spec_path.write_text(yaml.safe_dump(spec), encoding="utf-8")
    return cells_dir


class _FakeLlama:
    """Deterministic fake llama backend for unit tests."""

    def __init__(self) -> None:
        self._calls: list[list[dict[str, str]]] = []

    def create_chat_completion(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        self._calls.append(messages)
        prompt = messages[-1].get("content", "")
        if "Extract" in prompt or "extract" in prompt.lower():
            content = json.dumps({"topic": "demo", "value": 42})
        else:
            content = json.dumps({"verdict": "PASS", "result": "demo answer"})
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {"total_tokens": 12},
        }


class _FakeModelHost(ModelHost):
    """Model host that returns a fake Llama instead of loading GGUF files."""

    def __init__(self) -> None:
        super().__init__(model_specs={"fake_model": {"path": "/dev/null/fake.gguf"}})

    def get(self, model_id: str) -> Any:
        if model_id not in self._cache:
            self._cache[model_id] = _FakeLlama()
        return self._cache[model_id]


@pytest.fixture
def registry(temp_cells_dir: Path) -> CellRegistry:
    from egregore.governance.cell_protocol import STAGES

    reg = CellRegistry(cells_dir=temp_cells_dir).refresh()
    # Advance demo_cell through BCCBP stage gates so it appears delivered.
    spec = reg.get("demo_cell")
    for stage in STAGES:
        if stage == "plan":
            continue
        artifact_path = spec.artifacts.stage_gates.get(stage)
        if artifact_path:
            reg.controller.submit_artifact(
                "demo_cell", stage, artifact_path, validator_output="PASS"
            )
    return reg


@pytest.fixture
def executor(registry: CellRegistry) -> CellExecutor:
    return CellExecutor(registry=registry, model_host=_FakeModelHost())


# ---------------------------------------------------------------------------
# Model / spec tests
# ---------------------------------------------------------------------------
def test_cell_spec_accepts_string_taxonomy() -> None:
    spec = CellSpec.model_validate(
        {
            "cell_id": "test",
            "version": "1.0.0",
            "taxonomy": "university/engineering/software/python",
            "owner": "test",
            "type": "university",
            "pipeline": {"stages": []},
            "artifacts": {"stage_gates": {"plan": "x"}},
        }
    )
    assert spec.taxonomy_path() == "university/engineering/software/python"


def test_cell_spec_accepts_dict_taxonomy() -> None:
    spec = CellSpec.model_validate(
        {
            "cell_id": "test",
            "version": "1.0.0",
            "taxonomy": {
                "root": "guildhall",
                "branch": "building",
                "leaf": "carpentry",
            },
            "owner": "test",
            "type": "guild",
            "pipeline": {"stages": []},
            "artifacts": {"stage_gates": {"plan": "x"}},
        }
    )
    assert spec.taxonomy_path() == "guildhall/building/carpentry"


def test_cell_spec_rejects_mismatched_type_and_taxonomy() -> None:
    with pytest.raises(ValueError):
        CellSpec.model_validate(
            {
                "cell_id": "test",
                "version": "1.0.0",
                "taxonomy": "university/engineering/software/python",
                "owner": "test",
                "type": "guild",
                "pipeline": {"stages": []},
                "artifacts": {"stage_gates": {"plan": "x"}},
            }
        )


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------
def test_registry_loads_demo_cell(registry: CellRegistry) -> None:
    assert "demo_cell" in registry.specs
    spec = registry.get("demo_cell")
    assert spec.type == "university"
    assert spec.tier == 3


def test_registry_taxonomy_match(registry: CellRegistry) -> None:
    matches = registry.find_by_taxonomy("university/demo")
    assert len(matches) == 1
    assert matches[0].cell_id == "demo_cell"


def test_registry_least_loaded(registry: CellRegistry) -> None:
    candidates = registry.find_by_taxonomy("university/demo")
    chosen = registry.select_least_loaded(candidates)
    assert chosen is not None
    assert chosen.cell_id == "demo_cell"


# ---------------------------------------------------------------------------
# Executor tests
# ---------------------------------------------------------------------------
def test_executor_runs_pipeline(executor: CellExecutor) -> None:
    result = executor.run("demo_cell", {"input": "hello"})
    assert isinstance(result, CellResult)
    assert result.cell_id == "demo_cell"
    assert result.verdict == "PASS"
    assert "extract" in result.stages
    assert "answer" in result.stages
    assert "verify" in result.stages
    assert result.stages["extract"].parsed == {"topic": "demo", "value": 42}


def test_executor_topological_order_enforced(registry: CellRegistry) -> None:
    # The demo spec already has depends_on; this just verifies no KeyError.
    executor = CellExecutor(registry=registry, model_host=_FakeModelHost())
    result = executor.run("demo_cell", {"input": "test"})
    assert result.stages["answer"].parsed["verdict"] == "PASS"


# ---------------------------------------------------------------------------
# RFE adapter tests
# ---------------------------------------------------------------------------
def test_cell_result_to_stream_shape(executor: CellExecutor) -> None:
    result = executor.run("demo_cell", {"input": "hello"})
    stream = cell_result_to_stream(result, {"stream_type": "demo"})
    assert stream["type"] == "demo"
    assert stream["source_tier"] == 3
    assert stream["content"]["claim"] == "positive"
    assert stream["content"]["cell_id"] == "demo_cell"
    assert stream["provenance_hash"] == result.provenance_hash
    assert stream["decay"]["method"] == "unbounded"


def test_build_manifest(executor: CellExecutor) -> None:
    result = executor.run("demo_cell", {"input": "hello"})
    stream = cell_result_to_stream(result)
    manifest = build_manifest(case_id="case-123", streams=[stream])
    assert manifest["case_id"] == "case-123"
    assert len(manifest["streams"]) == 1


# ---------------------------------------------------------------------------
# Ombudsman router tests
# ---------------------------------------------------------------------------
def _make_test_app(registry: CellRegistry, executor: CellExecutor) -> FastAPI:
    from fastapi import Request
    from starlette.middleware.base import BaseHTTPMiddleware

    from egregore.models.user import UserIdentity

    class _TestAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            request.state.authenticated = True
            request.state.user = UserIdentity(
                tenant_id="test",
                user_id="test-admin",
                username="test-admin",
                email=None,
                roles=["admin"],
                vertical_grants=[],
                status="active",
            )
            request.state.roles = ["admin"]
            request.state.tenant_id = "test"
            request.state.user_id = "test-admin"
            request.state.role = "admin"
            return await call_next(request)

    app = FastAPI()
    app.add_middleware(_TestAuthMiddleware)
    app.state.cell_registry = registry
    app.state.cell_executor = executor
    app.include_router(ombudsman_router)
    return app


def test_ombudsman_list_cells(registry: CellRegistry, executor: CellExecutor) -> None:
    app = _make_test_app(registry, executor)
    client = TestClient(app)
    response = client.get("/api/v1/ombudsman/cells")
    assert response.status_code == 200
    data = response.json()
    assert any(c["cell_id"] == "demo_cell" for c in data["cells"])


def test_ombudsman_route(registry: CellRegistry, executor: CellExecutor) -> None:
    app = _make_test_app(registry, executor)
    client = TestClient(app)
    response = client.get("/api/v1/ombudsman/route/university/demo")
    assert response.status_code == 200
    assert response.json()["selected_cell"] == "demo_cell"


def test_ombudsman_dispatch_fuses(
    registry: CellRegistry, executor: CellExecutor
) -> None:
    app = _make_test_app(registry, executor)
    client = TestClient(app)
    response = client.post(
        "/api/v1/ombudsman/dispatch",
        json={
            "taxonomy": "university/demo",
            "input": "hello",
            "fuse": True,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "report" in data
    assert "report_hash" in data
    assert "version_id" in data
    assert len(data["streams"]) >= 1


def test_ombudsman_dispatch_streams_only(
    registry: CellRegistry, executor: CellExecutor
) -> None:
    app = _make_test_app(registry, executor)
    client = TestClient(app)
    response = client.post(
        "/api/v1/ombudsman/dispatch",
        json={
            "taxonomy": "university/demo",
            "input": "hello",
            "fuse": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "streams_only"
    assert "streams" in data
    assert "report" not in data


def test_ombudsman_advisory_call(
    registry: CellRegistry, executor: CellExecutor
) -> None:
    # Add advisory relationship so the call is permitted.
    spec = registry.get("demo_cell")
    spec.advisory_cells = ["demo_cell"]

    app = _make_test_app(registry, executor)
    client = TestClient(app)
    response = client.post(
        "/api/v1/ombudsman/advisory/call",
        json={
            "caller_cell_id": "demo_cell",
            "target_cell_id": "demo_cell",
            "input": "advisory input",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["target_cell_id"] == "demo_cell"
    assert "stream" in data


def test_ombudsman_advisory_call_forbidden(
    registry: CellRegistry, executor: CellExecutor
) -> None:
    app = _make_test_app(registry, executor)
    client = TestClient(app)
    response = client.post(
        "/api/v1/ombudsman/advisory/call",
        json={
            "caller_cell_id": "demo_cell",
            "target_cell_id": "demo_cell",
            "input": "advisory input",
        },
    )
    assert response.status_code == 403
