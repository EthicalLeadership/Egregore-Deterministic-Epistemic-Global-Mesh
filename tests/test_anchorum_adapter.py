"""Tests for AnchorumAdapter: verifies wrapping, hashing, and audit log."""

import json
import pytest

from egregore.interface.anchorum_adapter import AnchorumAdapter, RawAnchorumOutput


class MockEngine:
    """Mock LegalReasoningEngine with a configurable analyze method."""
    def __init__(self, result=None):
        self.result = result or {
            "case_id": "case_1",
            "applicable_rules": [],
            "inference_chain": [],
        }

    def analyze(self, ir, case_id):
        return self.result


@pytest.fixture
def mock_engine():
    return MockEngine()


@pytest.fixture
def adapter(mock_engine, tmp_path):
    return AnchorumAdapter(
        engine=mock_engine,
        raw_output_dir=tmp_path / "raw_outputs",
    )


def test_adapter_initialization_creates_output_dir(adapter, tmp_path):
    assert (tmp_path / "raw_outputs").exists()


def test_compute_hash_is_deterministic(adapter):
    data = {"a": 1, "b": [2, 3]}
    h1 = adapter._compute_hash(data)
    h2 = adapter._compute_hash(data)
    assert h1 == h2
    assert len(h1) == 64


def test_run_legal_analysis_returns_raw_output(adapter, mock_engine):
    # Use a serializable fake IR (dict), not object()
    fake_ir = {"facts": []}
    raw = adapter.run_legal_analysis(fake_ir, "case_1")

    assert isinstance(raw, RawAnchorumOutput)
    assert raw.tool_name == "legal_analysis"

    log_file = adapter.raw_output_dir / "raw_outputs.jsonl"
    assert log_file.exists()
    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 1
    stored = json.loads(lines[0])
    assert stored["tool_name"] == "legal_analysis"
    assert stored["raw_payload"]["output"] == mock_engine.result


def test_run_reproducible_fusion_returns_raw_output(adapter, monkeypatch):
    fake_manifest = {"streams": []}
    fake_config = {"debug": False}
    fake_result = {"report": {}, "report_hash": "abc"}

    import egregore.interface.anchorum_adapter as adapter_module
    monkeypatch.setattr(adapter_module, "reproducible_fusion", lambda manifest, config=None: fake_result)

    raw = adapter.run_reproducible_fusion(fake_manifest, fake_config)

    assert isinstance(raw, RawAnchorumOutput)
    assert raw.tool_name == "reproducible_fusion"
    log_file = adapter.raw_output_dir / "raw_outputs.jsonl"
    assert log_file.exists()
    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 1
    stored = json.loads(lines[0])
    assert stored["raw_payload"]["output"] == fake_result
