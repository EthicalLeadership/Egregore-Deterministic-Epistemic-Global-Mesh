"""Tests for the factory replay harness (Phase 7)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
fr = importlib.import_module("factory_replay")


def _resp(output: str) -> dict:
    return {"final_output": output, "qc": {"terminal_state": "SHIP"}, "provenance": {}}


def _ev(seq: int, event_type: str, **fields) -> dict:
    return {"seq_no": seq, "ts": "t", "ts_ns": seq, "event_type": event_type, **fields}


# ---------------------------------------------------------------------------
# strip_volatile
# ---------------------------------------------------------------------------
def test_strip_volatile_removes_only_volatile_keys():
    ev = _ev(1, "factory.station", station="cnc", latency_ms=99.1, vram_free_mb=2600,
             run_id="abc", policy_hash="hash123", tokens=5)
    stripped = fr.strip_volatile(ev)
    assert "latency_ms" not in stripped
    assert "vram_free_mb" not in stripped
    assert "run_id" not in stripped
    assert "ts_ns" not in stripped
    assert stripped["policy_hash"] == "hash123"  # regime marker is kept
    assert stripped["station"] == "cnc"


# ---------------------------------------------------------------------------
# compare_runs verdicts
# ---------------------------------------------------------------------------
def test_deterministic_when_identical():
    trace = [
        _ev(1, "factory.envelope.in", mode="case_report", payload_bytes=100),
        _ev(2, "factory.station", station="cnc", tokens=50),
        _ev(3, "factory.run.outcome", ok=True),
    ]
    verdict = fr.compare_runs(_resp("same"), _resp("same"), trace, [dict(e) for e in trace])
    assert verdict["deterministic"] is True
    assert verdict["output_identical"] is True
    assert verdict["trace_diffs"] == []


def test_volatile_differences_still_deterministic():
    trace_a = [_ev(1, "factory.station", station="cnc", latency_ms=10.0, run_id="a")]
    trace_b = [_ev(1, "factory.station", station="cnc", latency_ms=99.9, run_id="b")]
    verdict = fr.compare_runs(_resp("same"), _resp("same"), trace_a, trace_b)
    assert verdict["deterministic"] is True  # latency/run_id stripped


def test_diverged_on_output_difference():
    verdict = fr.compare_runs(_resp("output A"), _resp("output B"), [], [])
    assert verdict["deterministic"] is False
    assert verdict["output_identical"] is False


def test_diverged_on_trace_event_count():
    trace_a = [_ev(1, "factory.station", station="cnc")]
    trace_b = [
        _ev(1, "factory.station", station="cnc"),
        _ev(2, "factory.station", station="qc"),
    ]
    verdict = fr.compare_runs(_resp("same"), _resp("same"), trace_a, trace_b)
    assert verdict["deterministic"] is False
    assert verdict["trace_diffs"][0]["kind"] == "event_count"


def test_diverged_on_payload_difference_reports_keys():
    trace_a = [_ev(1, "factory.station", station="cnc", tokens=50)]
    trace_b = [_ev(1, "factory.station", station="cnc", tokens=99)]
    verdict = fr.compare_runs(_resp("same"), _resp("same"), trace_a, trace_b)
    assert verdict["deterministic"] is False
    diff = verdict["trace_diffs"][0]
    assert diff["kind"] == "payload"
    assert diff["differing_keys"] == ["tokens"]


# ---------------------------------------------------------------------------
# run trace reading
# ---------------------------------------------------------------------------
def test_read_run_trace_filters_by_run_id_and_marker(tmp_path: Path):
    events = [
        _ev(1, "factory.envelope.in", run_id="target", ts_ns=100),
        _ev(2, "factory.station", run_id="other", ts_ns=101),
        _ev(3, "factory.station", run_id="target", ts_ns=50),   # before marker
        _ev(4, "factory.station", run_id="target", ts_ns=200),  # included
    ]
    (tmp_path / "factory_2026-08-02.jsonl").write_text(
        "\n".join(__import__("json").dumps(e) for e in events), encoding="utf-8"
    )
    trace = fr.read_run_trace(tmp_path, "target", after_ns=100)
    assert [e["seq_no"] for e in trace] == [1, 4]
