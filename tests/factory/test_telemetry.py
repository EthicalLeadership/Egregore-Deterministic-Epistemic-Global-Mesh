"""Tests for factory telemetry (Phase 1 measurement)."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from egregore.factory import telemetry
from egregore.factory.telemetry import (
    FactoryRecorder,
    NullRecorder,
    emit,
    new_run_context,
    telemetry_context,
)
from egregore.http_api.http.app import create_app

VALID_KEY = "a" * 64


@pytest.fixture(autouse=True)
def fresh_recorder(tmp_path: Any, monkeypatch: pytest.MonkeyPatch):
    """Point telemetry at a temp dir and reset the singleton per test."""
    monkeypatch.setenv("EGREGORE_FACTORY_TELEMETRY_DIR", str(tmp_path))
    monkeypatch.delenv("EGREGORE_FACTORY_TELEMETRY", raising=False)
    telemetry.reset_recorder()
    yield tmp_path
    telemetry.reset_recorder()


def _read_events(tmp_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in tmp_path.glob("factory_*.jsonl"):
        events.extend(json.loads(ln) for ln in path.read_text().splitlines() if ln.strip())
    return events


# ---------------------------------------------------------------------------
# Recorder unit tests
# ---------------------------------------------------------------------------
def test_recorder_writes_canonical_jsonl(tmp_path: Path):
    rec = FactoryRecorder(tmp_path)
    rec.record_event({"event_type": "a", "z": 1, "b": 2})
    rec.record_event({"event_type": "b"})
    events = rec.export_trace()
    assert len(events) == 2
    assert [e["seq_no"] for e in events] == [1, 2]
    # canonical: keys sorted on the wire
    line = next(tmp_path.glob("factory_*.jsonl")).read_text().splitlines()[0]
    parsed = json.loads(line)
    assert sorted(parsed.keys()) == list(parsed.keys())


def test_recorder_requires_event_type(tmp_path: Path):
    rec = FactoryRecorder(tmp_path)
    with pytest.raises(ValueError, match="event_type"):
        rec.record_event({"no": "type"})


def test_emit_merges_context(tmp_path: Path):
    token = telemetry_context.set(new_run_context(mode="test_mode", task_id="t1"))
    try:
        emit("factory.station", station="cnc", elapsed_ms=1.5, tokens=3, model_id="m", ok=True)
    finally:
        telemetry_context.reset(token)
    (event,) = _read_events(tmp_path)
    assert event["event_type"] == "factory.station"
    assert event["run_id"]
    assert event["mode"] == "test_mode"
    assert event["task_id"] == "t1"
    assert event["station"] == "cnc"


def test_emit_without_context_still_records(tmp_path: Path):
    emit("factory.station", station="cnc", elapsed_ms=1, tokens=1, model_id="m", ok=True)
    (event,) = _read_events(tmp_path)
    assert event["event_type"] == "factory.station"
    assert "run_id" not in event


def test_telemetry_off_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EGREGORE_FACTORY_TELEMETRY", "off")
    telemetry.reset_recorder()
    rec = telemetry.get_recorder()
    assert isinstance(rec, NullRecorder)
    emit("factory.station", station="cnc")
    assert list(tmp_path.glob("factory_*.jsonl")) == []


# ---------------------------------------------------------------------------
# Router integration: full event chain per run
# ---------------------------------------------------------------------------
class _StubResponse:
    """Minimal ChatResponse stand-in for EgregoreInferenceHost."""

    def __init__(self) -> None:
        self.message = type("M", (), {"content": "stub-output"})()
        self.usage = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
        self.finish_reason = "stop"
        self.m1_passed = self.m2_passed = self.m3_passed = self.m4_passed = True
        self.inference_id = "inf-test"


class _StubInferenceService:
    def execute(self, request: Any) -> _StubResponse:
        return _StubResponse()


@pytest.fixture
def factory_client(tmp_path: Any):
    from egregore.http_api.http.middleware import api_key_middleware

    api_key_middleware._API_KEYS = {VALID_KEY: ("default", "user", "admin")}

    app = create_app(build_container=False)
    profiles_path = Path(__file__).resolve().parents[2] / "config" / "factory_profiles.yaml"
    with open(profiles_path, encoding="utf-8") as f:
        profiles = yaml.safe_load(f)

    from egregore.interface.factory_router import EgregoreInferenceHost

    app.state.factory_model_host = EgregoreInferenceHost(
        model_specs=profiles.get("models", {}),
        inference_service=_StubInferenceService(),  # type: ignore[arg-type]
    )
    return TestClient(app)


def test_factory_run_emits_full_event_chain(factory_client: TestClient, fresh_recorder: Path):
    r = factory_client.post(
        "/api/v1/factory",
        json={"input": "write a hello world function"},
        headers={"X-API-Key": VALID_KEY},
    )
    assert r.status_code == 200

    events = _read_events(fresh_recorder)
    by_type: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        by_type.setdefault(ev["event_type"], []).append(ev)

    # All four event types present
    assert "factory.envelope.in" in by_type
    assert "factory.station" in by_type
    assert "factory.inference" in by_type
    assert "factory.run.outcome" in by_type

    # Correlated by one run_id
    run_ids = {ev["run_id"] for ev in events}
    assert len(run_ids) == 1

    # v2 pipeline: 7 stations + matching inference events
    assert len(by_type["factory.station"]) == 7
    assert len(by_type["factory.inference"]) == 7
    stations = {ev["station"] for ev in by_type["factory.station"]}
    assert stations == {
        "spec_synthesis", "scaffolding", "cnc", "static_analysis",
        "dynamic_test", "moral_compliance", "final_qc",
    }

    # Inference events carry the unflattened detail
    inf = by_type["factory.inference"][0]
    assert inf["prompt_tokens"] == 11
    assert inf["completion_tokens"] == 7
    assert inf["total_tokens"] == 18
    assert inf["m1"] is True and inf["m4"] is True
    assert inf["finish_reason"] == "stop"
    assert inf["inference_id"] == "inf-test"
    assert "station" in inf  # station attribution via context

    # Outcome summarizes the run
    outcome = by_type["factory.run.outcome"][0]
    assert outcome["ok"] is True
    assert outcome["total_tokens"] == 7 * 18
    assert sorted(outcome["stations_taken"]) == sorted(stations)


def test_envelope_run_carries_task_identity(factory_client: TestClient, fresh_recorder: Path):
    intake = factory_client.post(
        "/api/v1/factory/v1/intake",
        json={"source_type": "chat", "text": "summarize this document"},
        headers={"X-API-Key": VALID_KEY},
    )
    assert intake.status_code == 200
    envelope = intake.json()

    r = factory_client.post(
        "/api/v1/factory/v1/run",
        json={"envelope": envelope},
        headers={"X-API-Key": VALID_KEY},
    )
    assert r.status_code == 200

    events = _read_events(fresh_recorder)
    env_in = next(e for e in events if e["event_type"] == "factory.envelope.in")
    assert env_in["task_id"] == envelope["task_id"]
    assert env_in["task_fingerprint"]
    assert "chat" in env_in["task_type"]


# ---------------------------------------------------------------------------
# Bucketer
# ---------------------------------------------------------------------------
def test_histogram_bucketer(tmp_path: Path):
    scripts_dir = str(Path(__file__).resolve().parents[2] / "scripts")
    sys.path.insert(0, scripts_dir)
    try:
        fh = importlib.import_module("factory_histogram")
    finally:
        sys.path.remove(scripts_dir)

    def run_events(run_id: str, stations: int, tokens: int, ok: bool = True) -> list[dict]:
        evs = [
            {"event_type": "factory.station", "run_id": run_id, "station": f"s{i}",
             "elapsed_ms": 100, "tokens": tokens // max(stations, 1)}
            for i in range(stations)
        ]
        evs.append({
            "event_type": "factory.run.outcome", "run_id": run_id, "ok": ok,
            "stations_taken": [f"s{i}" for i in range(stations)],
            "total_elapsed_ms": 100 * stations, "total_tokens": tokens,
        })
        return evs

    events = (
        run_events("r-trivial", stations=1, tokens=100)
        + run_events("r-micro", stations=2, tokens=1000)
        + run_events("r-structured", stations=7, tokens=4000)
        + run_events("r-heavy", stations=7, tokens=9000)
        + run_events("r-failed", stations=3, tokens=500, ok=False)
    )
    (tmp_path / "factory_2026-08-02.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )

    histogram = fh.build_histogram(tmp_path)
    counts = {name: b["count"] for name, b in histogram["buckets"].items()}
    assert counts["trivial"] == 1
    assert counts["micro_solvable"] == 1
    assert counts["structured_final"] == 1
    assert counts["heavy"] == 2  # over token limit + failed run
    assert histogram["total_runs"] == 5
