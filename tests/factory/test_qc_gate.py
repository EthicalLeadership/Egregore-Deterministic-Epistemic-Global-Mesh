"""Tests for the fail-closed QC gate (Phase 2, Station 5)."""

from __future__ import annotations

import time
from typing import Any

import pytest

from egregore.factory import telemetry
from egregore.factory.qc_gate import (
    EgregoreCritic,
    QCGate,
    QCVerdict,
    Violation,
    run_deterministic_checks,
)

POLICY: dict[str, Any] = {
    "fail_closed": True,
    "confidence_threshold": 0.6,
    "rework_budget": 2,
    "critic_timeout_ms": 50,
    "critic_model": "qwen_1.5b",
    "critic_max_tokens": 64,
    "max_output_chars": 1000,
    "forbidden_patterns": ["as an ai language model"],
    "required_output_fields": ["module"],
}

GOOD_OUTPUT = "def add(a, b):\n    return a + b\n"


@pytest.fixture(autouse=True)
def _telemetry_tmp(tmp_path: Any, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EGREGORE_FACTORY_TELEMETRY_DIR", str(tmp_path))
    monkeypatch.delenv("EGREGORE_FACTORY_QC", raising=False)
    telemetry.reset_recorder()
    yield tmp_path
    telemetry.reset_recorder()


def _read_qc_events(tmp_path: Any) -> list[dict[str, Any]]:
    import json

    events: list[dict[str, Any]] = []
    for path in tmp_path.glob("factory_*.jsonl"):
        events.extend(
            json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()
        )
    return [e for e in events if e["event_type"] == "factory.qc.verdict"]


class PassCritic:
    def critique(self, **kwargs: Any) -> QCVerdict:
        return QCVerdict(
            verdict="PASS", confidence=0.9, violations=[],
            critic_model="stub", tier="critic",
        )


def _noop_rerun(prompt: str):
    return GOOD_OUTPUT, {"module": GOOD_OUTPUT}, None


def _gate(critic: Any = None, rerun: Any = None, policy: dict | None = None) -> QCGate:
    return QCGate(
        policy=policy or POLICY,
        critic=critic if critic is not None else PassCritic(),
        rerun_terminal=rerun or _noop_rerun,
    )


# ---------------------------------------------------------------------------
# Tier 1 — deterministic
# ---------------------------------------------------------------------------
def test_tier1_empty_output_fails():
    v = run_deterministic_checks("   ", policy=POLICY)
    assert any(x.constraint_id == "empty_output" for x in v)


def test_tier1_oversize_fails():
    v = run_deterministic_checks("x" * 2000, policy=POLICY)
    assert any(x.constraint_id == "output_too_long" for x in v)


def test_tier1_forbidden_pattern_fails():
    v = run_deterministic_checks("As an AI language model, I cannot", policy=POLICY)
    assert any(x.constraint_id == "forbidden_pattern" for x in v)


def test_tier1_m_flag_failure_fails():
    v = run_deterministic_checks(
        GOOD_OUTPUT, policy=POLICY,
        m_flags={"m1": True, "m2": True, "m3": False, "m4": True},
    )
    assert any(x.constraint_id == "governance_m_flags" for x in v)


def test_tier1_missing_required_field_fails():
    v = run_deterministic_checks(
        GOOD_OUTPUT, policy=POLICY, terminal_parsed={"other": 1},
    )
    assert any(x.constraint_id == "missing_required_fields" for x in v)


def test_tier1_clean_output_passes():
    v = run_deterministic_checks(
        GOOD_OUTPUT, policy=POLICY,
        m_flags={"m1": True, "m2": True, "m3": True, "m4": True},
        terminal_parsed={"module": GOOD_OUTPUT},
    )
    assert v == []


# ---------------------------------------------------------------------------
# Tier 2 — critic contract (via EgregoreCritic + stub host)
# ---------------------------------------------------------------------------
class StubHost:
    def __init__(self, reply: str = "", error: Exception | None = None, sleep_s: float = 0.0):
        self.reply = reply
        self.error = error
        self.sleep_s = sleep_s

    def execute(self, **kwargs: Any):
        if self.sleep_s:
            time.sleep(self.sleep_s)
        if self.error:
            raise self.error
        return self.reply, 10, "stub"


def _critic(host: StubHost) -> EgregoreCritic:
    return EgregoreCritic(host, model_id="qwen_1.5b", confidence_threshold=0.6)


def test_critic_malformed_verdict_fails():
    v = _critic(StubHost(reply="not json at all")).critique(
        output=GOOD_OUTPUT, constraints=[], max_tokens=64, timeout_ms=5000,
    )
    assert v.verdict == "FAIL"
    assert v.violations[0].constraint_id == "malformed_verdict"


def test_critic_invalid_verdict_value_fails():
    v = _critic(StubHost(reply='{"verdict": "MAYBE", "confidence": 0.9}')).critique(
        output=GOOD_OUTPUT, constraints=[], max_tokens=64, timeout_ms=5000,
    )
    assert v.verdict == "FAIL"
    assert v.violations[0].constraint_id == "malformed_verdict"


def test_critic_low_confidence_fails():
    v = _critic(StubHost(reply='{"verdict": "PASS", "confidence": 0.2, "violations": []}')).critique(
        output=GOOD_OUTPUT, constraints=[], max_tokens=64, timeout_ms=5000,
    )
    assert v.verdict == "FAIL"
    assert v.violations[0].constraint_id == "low_confidence"


def test_critic_exception_fails():
    v = _critic(StubHost(error=RuntimeError("boom"))).critique(
        output=GOOD_OUTPUT, constraints=[], max_tokens=64, timeout_ms=5000,
    )
    assert v.verdict == "FAIL"
    assert v.violations[0].constraint_id == "critic_error"


def test_critic_timeout_fails():
    v = _critic(StubHost(
        reply='{"verdict": "PASS", "confidence": 0.9, "violations": []}',
        sleep_s=0.1,
    )).critique(output=GOOD_OUTPUT, constraints=[], max_tokens=64, timeout_ms=50)
    assert v.verdict == "FAIL"
    assert v.violations[0].constraint_id == "critic_timeout"


def test_critic_valid_pass():
    v = _critic(StubHost(
        reply='{"verdict": "PASS", "confidence": 0.95, "violations": []}'
    )).critique(output=GOOD_OUTPUT, constraints=[], max_tokens=64, timeout_ms=5000)
    assert v.verdict == "PASS"
    assert v.confidence == 0.95


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
class FailThenPassCritic:
    def __init__(self, fails: int):
        self.fails_remaining = fails
        self.calls = 0

    def critique(self, **kwargs: Any) -> QCVerdict:
        self.calls += 1
        if self.fails_remaining > 0:
            self.fails_remaining -= 1
            return QCVerdict(
                verdict="FAIL", confidence=0.9,
                violations=[Violation(constraint_id="incoherent", evidence="stub fail")],
                critic_model="stub", tier="critic",
            )
        return QCVerdict(
            verdict="PASS", confidence=0.9, violations=[],
            critic_model="stub", tier="critic",
        )


def test_rework_loop_terminates_and_ships(_telemetry_tmp):
    critic = FailThenPassCritic(fails=1)
    outcome = _gate(critic=critic).evaluate(
        output=GOOD_OUTPUT, constraints=["be coherent"],
        terminal_parsed={"module": GOOD_OUTPUT},
    )
    assert outcome.terminal_state == "SHIP"
    assert outcome.reworks_used == 1
    assert outcome.m4_emission == "EQUIVALENT"
    assert critic.calls == 2


def test_budget_exhausted_blocks_after_escalation(_telemetry_tmp):
    critic = FailThenPassCritic(fails=99)  # never passes
    outcome = _gate(critic=critic).evaluate(
        output=GOOD_OUTPUT, constraints=["be coherent"],
        terminal_parsed={"module": GOOD_OUTPUT},
    )
    assert outcome.terminal_state == "BLOCKED"
    assert outcome.m4_emission == "DIVERGED"
    assert outcome.final_output is None
    assert outcome.reworks_used == 2  # exactly the budget
    assert outcome.escalated is True
    # budget 2 reworks + initial + 1 escalation = 4 critic calls
    assert critic.calls == 4


def test_escalation_pass_ships_with_flag(_telemetry_tmp):
    critic = FailThenPassCritic(fails=3)  # fails initial + 2 reworks, passes escalation
    outcome = _gate(critic=critic).evaluate(
        output=GOOD_OUTPUT, constraints=["be coherent"],
        terminal_parsed={"module": GOOD_OUTPUT},
    )
    assert outcome.terminal_state == "SHIP"
    assert outcome.escalated is True
    assert outcome.reworks_used == 2


def test_tier1_failure_skips_critic(_telemetry_tmp):
    class CountingCritic(PassCritic):
        calls = 0

        def critique(self, **kwargs: Any) -> QCVerdict:
            self.calls += 1
            return super().critique(**kwargs)

    critic = CountingCritic()
    outcome = _gate(critic=critic).evaluate(
        output="", constraints=[], terminal_parsed=None,
    )
    # deterministic FAIL → rework → rerun returns GOOD_OUTPUT → Tier1+Tier2 pass
    assert outcome.terminal_state == "SHIP"
    assert outcome.reworks_used == 1
    # critic consulted exactly once (only after the reworked output passed Tier 1)
    assert critic.calls == 1


def test_m3_verdict_never_reenters_as_input(_telemetry_tmp):
    """Rework prompts get typed violations only — never the verdict object."""
    prompts: list[str] = []
    critic = FailThenPassCritic(fails=1)

    def rerun(prompt: str):
        prompts.append(prompt)
        return GOOD_OUTPUT, {"module": GOOD_OUTPUT}, None

    _gate(critic=critic, rerun=rerun).evaluate(
        output=GOOD_OUTPUT, constraints=["x"], terminal_parsed={"module": GOOD_OUTPUT},
    )
    assert len(prompts) == 1
    assert '"constraint_id"' in prompts[0]  # typed violations present
    assert "QCVerdict" not in prompts[0]    # verdict object never serialized in
    assert '"confidence"' not in prompts[0]  # no verdict metadata leaks


def test_telemetry_emitted_per_verdict(_telemetry_tmp):
    critic = FailThenPassCritic(fails=1)
    _gate(critic=critic).evaluate(
        output=GOOD_OUTPUT, constraints=[], terminal_parsed={"module": GOOD_OUTPUT},
    )
    events = _read_qc_events(_telemetry_tmp)
    assert len(events) == 2
    assert events[0]["verdict"] == "FAIL"
    assert events[1]["verdict"] == "PASS"


def test_kill_switch_bypasses_with_record(_telemetry_tmp, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EGREGORE_FACTORY_QC", "off")
    outcome = _gate().evaluate(output=GOOD_OUTPUT, constraints=[])
    assert outcome.terminal_state == "SHIP"
    assert outcome.bypassed is True
    events = _read_qc_events(_telemetry_tmp)
    assert len(events) == 1
    assert events[0]["bypassed"] is True
    assert events[0]["tier"] == "bypassed"
