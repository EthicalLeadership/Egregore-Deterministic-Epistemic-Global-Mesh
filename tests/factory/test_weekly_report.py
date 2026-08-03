"""Tests for the weekly report builder."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
wr = importlib.import_module("factory_weekly_report")


def _run(rid: str, policy_hash: str, state: str, source: str = "http",
         payload_bytes: int = 500, violations: list[str] | None = None,
         critic_ms: float = 100.0) -> list[dict]:
    events = [
        {"event_type": "factory.envelope.in", "run_id": rid, "source_type": source,
         "payload_bytes": payload_bytes, "ts": "2026-08-02T10:00:00"},
    ]
    if violations is not None:
        events.append({
            "event_type": "factory.qc.verdict", "run_id": rid, "tier": "critic",
            "verdict": "FAIL" if violations else "PASS", "latency_ms": critic_ms,
            "ts": "2026-08-02T10:01:00",
            "violations": [
                {"constraint_id": cid, "evidence": "x", "severity": "hard"}
                for cid in violations
            ],
        })
    events.append({
        "event_type": "factory.run.outcome", "run_id": rid, "policy_hash": policy_hash,
        "qc": {"terminal_state": state}, "ts": "2026-08-02T10:02:00",
    })
    return events


def test_family_breakdown_per_policy_hash():
    events = (
        _run("r1", "hashA", "BLOCKED", violations=["empty_output", "empty_output"])
        + _run("r2", "hashA", "SHIP")
        + _run("r3", "hashB", "BLOCKED", violations=["forbidden_pattern"])
    )
    report = wr.build_report(events)
    a = report["by_policy_hash"]["hashA"]
    assert a["runs"] == 2 and a["blocked"] == 1 and a["blocked_rate"] == "50.0%"
    assert a["fail_families"] == {"compression": 2}
    b = report["by_policy_hash"]["hashB"]
    assert b["fail_families"] == {"model_contract": 1}


def test_traffic_split_synthetic_vs_real():
    events = (
        _run("s1", "h", "BLOCKED", payload_bytes=20)   # tiny -> synthetic
        + _run("s2", "h", "SHIP", payload_bytes=30)
        + _run("r1", "h", "BLOCKED", payload_bytes=900)  # real
        + _run("r2", "h", "SHIP", payload_bytes=800)
        + _run("r3", "h", "SHIP", payload_bytes=700)
    )
    report = wr.build_report(events)
    assert report["traffic"]["synthetic"]["total"] == 2
    assert report["traffic"]["synthetic"]["blocked_rate"] == "50.0%"
    assert report["traffic"]["real"]["total"] == 3
    assert report["traffic"]["real"]["blocked_rate"] == "33.3%"


def test_critic_p95_by_day():
    events = []
    for i, ms in enumerate([100, 200, 300, 400, 5000]):
        events.extend(_run(f"r{i}", "h", "SHIP", violations=[], critic_ms=float(ms)))
    report = wr.build_report(events)
    assert report["critic_p95_by_day"]["2026-08-02"] == 5000.0


def test_unknown_constraint_id_defaults_to_model_contract():
    assert wr.family_of("write python function number 3") == "model_contract"
    assert wr.family_of("0") == "model_contract"
    assert wr.family_of("vram_insufficient") == "infrastructure"
