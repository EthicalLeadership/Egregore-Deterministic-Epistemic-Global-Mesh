"""Tests for the factory policy loader (Phase 3 governance)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from egregore.factory import policy as policy_mod
from egregore.factory import telemetry
from egregore.factory.policy import PolicyError, load_policy
from egregore.http_api.http.app import create_app

VALID_KEY = "a" * 64

GOOD_POLICY = {
    "version": 1,
    "qc": {
        "fail_closed": True,
        "confidence_threshold": 0.7,
        "rework_budget": 3,
        "escalate_to": "heavy",
        "critic_timeout_ms": 15000,
        "critic_max_tokens": 256,
        "deterministic_first": True,
    },
    "escalation": {
        "path": ["micro", "standard", "heavy"],
        "never_skip_station": True,
        "max_heavy_escalations_per_run": 1,
    },
}


@pytest.fixture(autouse=True)
def _clean(tmp_path: Any, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("EGREGORE_FACTORY_POLICY", raising=False)
    for var in policy_mod._ENV_OVERRIDES:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("EGREGORE_FACTORY_TELEMETRY_DIR", str(tmp_path / "telemetry"))
    telemetry.reset_recorder()
    policy_mod.reset_policy_cache()
    yield tmp_path
    policy_mod.reset_policy_cache()
    telemetry.reset_recorder()


def _write(tmp_path: Path, data: Any) -> Path:
    path = tmp_path / "factory_policy.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def test_load_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = _write(tmp_path, GOOD_POLICY)
    monkeypatch.setenv("EGREGORE_FACTORY_POLICY", str(path))
    loaded = load_policy()
    assert loaded.data["qc"]["rework_budget"] == 3
    assert loaded.data["qc"]["confidence_threshold"] == 0.7
    assert loaded.path == path
    assert len(loaded.policy_hash) == 64


def test_missing_file_uses_code_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EGREGORE_FACTORY_POLICY", str(tmp_path / "nope.json"))
    loaded = load_policy()
    assert loaded.data["qc"]["rework_budget"] == 2  # code default
    assert loaded.path is None


def test_malformed_json_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = _write(tmp_path, "{not json")
    monkeypatch.setenv("EGREGORE_FACTORY_POLICY", str(path))
    with pytest.raises(PolicyError, match="not valid JSON"):
        load_policy()


def test_missing_qc_block_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = _write(tmp_path, {"version": 1, "escalation": {}})
    monkeypatch.setenv("EGREGORE_FACTORY_POLICY", str(path))
    with pytest.raises(PolicyError, match="missing required 'qc'"):
        load_policy()


def test_non_object_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = _write(tmp_path, '["qc"]')
    monkeypatch.setenv("EGREGORE_FACTORY_POLICY", str(path))
    with pytest.raises(PolicyError, match="must be a JSON object"):
        load_policy()


# ---------------------------------------------------------------------------
# Precedence: env > file > code defaults
# ---------------------------------------------------------------------------
def test_env_beats_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = _write(tmp_path, GOOD_POLICY)  # file says budget 3
    monkeypatch.setenv("EGREGORE_FACTORY_POLICY", str(path))
    monkeypatch.setenv("EGREGORE_FACTORY_QC_REWORK_BUDGET", "5")
    loaded = load_policy()
    assert loaded.data["qc"]["rework_budget"] == 5
    assert loaded.overrides == {"qc.rework_budget": 5}


def test_file_beats_code_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = _write(tmp_path, {"qc": {"rework_budget": 7}})
    monkeypatch.setenv("EGREGORE_FACTORY_POLICY", str(path))
    loaded = load_policy()
    assert loaded.data["qc"]["rework_budget"] == 7
    # untouched keys still fall back to code defaults
    assert loaded.data["qc"]["critic_timeout_ms"] == 60000


def test_env_override_changes_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = _write(tmp_path, GOOD_POLICY)
    monkeypatch.setenv("EGREGORE_FACTORY_POLICY", str(path))
    plain = load_policy().policy_hash
    policy_mod.reset_policy_cache()
    monkeypatch.setenv("EGREGORE_FACTORY_QC_REWORK_BUDGET", "9")
    overridden = load_policy().policy_hash
    assert plain != overridden


def test_bad_env_override_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EGREGORE_FACTORY_QC_REWORK_BUDGET", "two")
    with pytest.raises(PolicyError, match="not a valid"):
        load_policy()


# ---------------------------------------------------------------------------
# Hot reload + hash stability
# ---------------------------------------------------------------------------
def test_mtime_hot_reload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = _write(tmp_path, {"qc": {"rework_budget": 2}})
    monkeypatch.setenv("EGREGORE_FACTORY_POLICY", str(path))
    assert load_policy().data["qc"]["rework_budget"] == 2

    time.sleep(0.01)
    path.write_text(json.dumps({"qc": {"rework_budget": 4}}), encoding="utf-8")
    import os

    os.utime(path, (time.time() + 1, time.time() + 1))  # ensure mtime changes
    assert load_policy().data["qc"]["rework_budget"] == 4


def test_hash_stable_across_identical_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = _write(tmp_path, GOOD_POLICY)
    monkeypatch.setenv("EGREGORE_FACTORY_POLICY", str(path))
    h1 = load_policy().policy_hash
    policy_mod.reset_policy_cache()
    h2 = load_policy().policy_hash
    assert h1 == h2


# ---------------------------------------------------------------------------
# Router integration: malformed policy blocks the whole run
# ---------------------------------------------------------------------------
@pytest.fixture
def factory_client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch):
    from egregore.http_api.http.middleware import api_key_middleware

    monkeypatch.setenv("EGREGORE_FACTORY_TELEMETRY_DIR", str(tmp_path / "telemetry"))
    telemetry.reset_recorder()
    api_key_middleware._API_KEYS = {VALID_KEY: ("default", "user", "admin")}

    app = create_app(build_container=False)
    profiles_path = Path(__file__).resolve().parents[2] / "config" / "factory_profiles.yaml"
    with open(profiles_path, encoding="utf-8") as f:
        profiles = yaml.safe_load(f)

    from egregore.interface.factory_router import EgregoreInferenceHost

    class _Resp:
        def __init__(self) -> None:
            self.message = type("M", (), {"content": '{"module": "ok"}'})()
            self.usage = {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}
            self.finish_reason = "stop"
            self.m1_passed = self.m2_passed = self.m3_passed = self.m4_passed = True
            self.inference_id = "inf-stub"

    class _Svc:
        def execute(self, request: Any) -> _Resp:
            return _Resp()

    app.state.factory_model_host = EgregoreInferenceHost(
        model_specs=profiles.get("models", {}),
        inference_service=_Svc(),  # type: ignore[arg-type]
    )
    return TestClient(app)


def test_malformed_policy_blocks_run(
    factory_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    bad = _write(tmp_path, "{corrupt")
    monkeypatch.setenv("EGREGORE_FACTORY_POLICY", str(bad))
    policy_mod.reset_policy_cache()

    r = factory_client.post(
        "/api/v1/factory",
        json={"input": "write hello world"},
        headers={"X-API-Key": VALID_KEY},
    )
    assert r.status_code == 200  # run completed; the BLOCK is data
    data = r.json()
    assert data["qc"]["terminal_state"] == "BLOCKED"
    assert data["qc"]["m4_emission"] == "DIVERGED"
    assert data["qc"]["violations"][0]["constraint_id"] == "policy_malformed"
    assert data["stations"] == {}  # no station ever ran
    assert "BLOCKED" in data["final_output"]


def test_outcome_event_carries_policy_hash(
    factory_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    good = _write(tmp_path, GOOD_POLICY)
    monkeypatch.setenv("EGREGORE_FACTORY_POLICY", str(good))
    monkeypatch.setenv("EGREGORE_FACTORY_QC", "off")  # bypass gate; policy still loads
    policy_mod.reset_policy_cache()

    r = factory_client.post(
        "/api/v1/factory",
        json={"input": "hi"},
        headers={"X-API-Key": VALID_KEY},
    )
    assert r.status_code == 200

    expected_hash = load_policy().policy_hash
    events = [
        json.loads(ln)
        for p in (tmp_path / "telemetry").glob("factory_*.jsonl")
        for ln in p.read_text().splitlines()
        if ln.strip()
    ]
    outcome = next(e for e in events if e["event_type"] == "factory.run.outcome")
    assert outcome["policy_hash"] == expected_hash
    envelope = next(e for e in events if e["event_type"] == "factory.envelope.in")
    assert envelope["policy_hash"] == expected_hash


def test_env_override_emits_telemetry(
    factory_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    good = _write(tmp_path, GOOD_POLICY)
    monkeypatch.setenv("EGREGORE_FACTORY_POLICY", str(good))
    monkeypatch.setenv("EGREGORE_FACTORY_QC_REWORK_BUDGET", "1")
    monkeypatch.setenv("EGREGORE_FACTORY_QC", "off")
    policy_mod.reset_policy_cache()

    factory_client.post(
        "/api/v1/factory", json={"input": "hi"}, headers={"X-API-Key": VALID_KEY}
    )
    events = [
        json.loads(ln)
        for p in (tmp_path / "telemetry").glob("factory_*.jsonl")
        for ln in p.read_text().splitlines()
        if ln.strip()
    ]
    override = next(e for e in events if e["event_type"] == "factory.policy.override")
    assert override["key"] == "qc.rework_budget"
    assert override["value"] == 1
    assert override["source"] == "env"


def test_gate_respects_budget_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """End-to-end precedence: env budget=0 means the gate escalates immediately."""
    monkeypatch.setenv("EGREGORE_FACTORY_QC_REWORK_BUDGET", "0")
    policy_mod.reset_policy_cache()

    from egregore.factory.qc_gate import QCGate, QCVerdict, Violation

    class AlwaysFail:
        calls = 0

        def critique(self, **kwargs: Any) -> QCVerdict:
            self.calls += 1
            return QCVerdict(
                verdict="FAIL", confidence=0.9,
                violations=[Violation(constraint_id="x", evidence="no")],
                critic_model="stub", tier="critic",
            )

    reruns: list[str] = []

    def rerun(prompt: str):
        reruns.append(prompt)
        return "out", {"module": "out"}, None

    qc = load_policy().data["qc"]
    gate = QCGate(policy=qc, critic=AlwaysFail(), rerun_terminal=rerun)
    outcome = gate.evaluate(output="bad", constraints=[], terminal_parsed={"module": "bad"})
    assert outcome.terminal_state == "BLOCKED"
    assert outcome.reworks_used == 0  # budget 0 honored: no rework, straight to escalation
    assert outcome.escalated is True
    assert len(reruns) == 1  # exactly the escalation pass
