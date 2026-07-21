"""Tests for the Interface Synod dashboard."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from egregore.tooling.dashboard.server import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def temp_report(tmp_path: Path) -> Path:
    """Write a minimal enriched aggregate report and point the dashboard at it."""
    report = {
        "timestamp_ns": 1_700_000_000_000_000_000,
        "modules_scanned": 2,
        "modules_with_manifests": 1,
        "failed": False,
        "module_results": [
            {
                "module_id": "egregore.shared",
                "name": "shared",
                "layer": "shared",
                "pipeline_class": "fast",
                "returncode": 0,
                "terminal": False,
                "attestation_badge": "NOT_TERMINAL",
                "violations": [],
                "build_timestamp": "2023-11-14T00:00:00+00:00",
                "m1": {"status": "PASS", "violations": [], "metadata": {}},
                "m2": {"status": "NOT_VERIFIED", "violations": [], "metadata": {}},
                "m3": {"status": "NOT_ENFORCED", "violations": [], "metadata": {}},
                "m4": {"status": "DIVERGED", "note": "No spec file provided"},
                "m5": {"status": "NOT_ENFORCED", "violations": [], "metadata": {}},
            },
            {
                "module_id": "egregore.application.heavy",
                "name": "heavy",
                "layer": "application",
                "pipeline_class": "standard",
                "returncode": 0,
                "terminal": True,
                "attestation_badge": "SIGNED",
                "decom_manifest": {
                    "dependencies": ["egregore.shared"],
                    "procedure": "docs/decom/heavy.md",
                    "test_log": "logs/decom/heavy.log",
                    "attestation": {
                        "signature": "sig",
                        "signer_id": "dsb-chair",
                        "timestamp": "2026-07-19T00:00:00Z",
                    },
                },
                "violations": [],
                "build_timestamp": "2023-11-14T00:00:00+00:00",
                "m1": {"status": "PASS", "violations": [], "metadata": {}},
                "m2": {"status": "PASS", "violations": [], "metadata": {}},
                "m3": {
                    "status": "PASS",
                    "violations": [],
                    "metadata": {"terminal": True},
                },
                "m4": {"status": "DIVERGED", "note": "No spec file provided"},
                "m5": {"status": "NOT_ENFORCED", "violations": [], "metadata": {}},
            },
        ],
        "graph": {
            "status": "PASS",
            "graph": {
                "egregore.application.heavy": {
                    "dependencies": [
                        {"module": "egregore.shared", "version": "0.1.0", "hash": ""}
                    ],
                },
                "egregore.shared": {"dependencies": []},
            },
        },
    }
    report_path = tmp_path / "aggregate_report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return tmp_path


def test_dashboard_index(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Interface Synod" in response.text


def test_dashboard_report_not_found(client: TestClient) -> None:
    # Ensure default path does not accidentally find a report.
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["BLACKSTAR_SANDBOX_OUTPUT"] = tmp
        response = client.get("/api/report")
        assert response.status_code == 404
    del os.environ["BLACKSTAR_SANDBOX_OUTPUT"]


def test_dashboard_report_endpoint(client: TestClient, temp_report: Path) -> None:
    os.environ["BLACKSTAR_SANDBOX_OUTPUT"] = str(temp_report)
    response = client.get("/api/report")
    assert response.status_code == 200
    data = response.json()
    assert data["modules_scanned"] == 2
    assert any(m["module_id"] == "egregore.shared" for m in data["module_results"])
    del os.environ["BLACKSTAR_SANDBOX_OUTPUT"]


def test_dashboard_module_endpoint(client: TestClient, temp_report: Path) -> None:
    os.environ["BLACKSTAR_SANDBOX_OUTPUT"] = str(temp_report)
    response = client.get("/api/modules/egregore.application.heavy")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "heavy"
    assert data["terminal"] is True
    assert data["attestation_badge"] == "SIGNED"
    assert data["decom_manifest"]["attestation"]["signer_id"] == "dsb-chair"
    del os.environ["BLACKSTAR_SANDBOX_OUTPUT"]


def test_dashboard_module_endpoint_short_name(
    client: TestClient, temp_report: Path
) -> None:
    os.environ["BLACKSTAR_SANDBOX_OUTPUT"] = str(temp_report)
    response = client.get("/api/modules/shared")
    assert response.status_code == 200
    data = response.json()
    assert data["module_id"] == "egregore.shared"
    del os.environ["BLACKSTAR_SANDBOX_OUTPUT"]


def test_dashboard_module_not_found(client: TestClient, temp_report: Path) -> None:
    os.environ["BLACKSTAR_SANDBOX_OUTPUT"] = str(temp_report)
    response = client.get("/api/modules/egregore.missing")
    assert response.status_code == 404
    del os.environ["BLACKSTAR_SANDBOX_OUTPUT"]
