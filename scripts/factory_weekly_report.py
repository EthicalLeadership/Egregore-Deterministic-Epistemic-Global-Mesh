#!/usr/bin/env python3
"""Factory weekly report — the histogram-week deliverables in one command.

Reads factory telemetry JSONL and prints:
  1. FAIL family breakdown sliced by policy_hash (Phase 4-vs-5 decision table)
  2. BLOCKED rate (real traffic vs gauntlet/synthetic)
  3. Critic latency trend per day
  4. Bucket counts (trivial / micro / structured / heavy)

Usage:
    .venv/bin/python scripts/factory_weekly_report.py [--dir report/factory_telemetry]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

# Failure-family classification for the Phase 4/5 decision:
# retrieval-class = output missing facts that exist in stores
# compression-class = constraints dropped / context bloated between stations
FAMILY_MAP = {
    "empty_output": "compression",
    "output_too_long": "compression",
    "missing_required_fields": "compression",
    "malformed_verdict": "model_contract",
    "forbidden_pattern": "model_contract",
    "governance_m_flags": "governance",
    "critic_error": "infrastructure",
    "critic_timeout": "infrastructure",
    "gate_error": "infrastructure",
    "vram_insufficient": "infrastructure",
    "low_confidence": "critic_calibration",
    "policy_malformed": "governance",
}


def load_events(directory: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in sorted(directory.glob("factory_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


def family_of(constraint_id: str) -> str:
    if constraint_id in FAMILY_MAP:
        return FAMILY_MAP[constraint_id]
    # Critic-invented constraint ids (e.g. echoing the constraint text, "0",
    # "CONSTRAINTS[0]") mean the model judged content quality — model_contract.
    return "model_contract"


def build_report(events: list[dict[str, Any]]) -> dict[str, Any]:  # noqa: C901 — report aggregation, complexity acceptable
    runs: dict[str, dict[str, Any]] = {}
    for ev in events:
        rid = ev.get("run_id")
        if rid:
            runs.setdefault(rid, []).append(ev)

    # --- FAIL families per policy_hash (regime) ---
    families: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    # --- per-run outcome stats ---
    regime_runs: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "blocked": 0, "ship": 0}
    )
    synthetic = {"total": 0, "blocked": 0}
    real = {"total": 0, "blocked": 0}
    critic_by_day: dict[str, list[float]] = defaultdict(list)

    for run in runs.values():
        outcome = next((e for e in run if e["event_type"] == "factory.run.outcome"), None)
        policy_hash = (outcome or {}).get("policy_hash") or "pre-policy"
        is_synthetic = False

        envelope = next((e for e in run if e["event_type"] == "factory.envelope.in"), None)
        if envelope and envelope.get("source_type") == "http" and envelope.get("payload_bytes", 0) < 120:
            is_synthetic = True  # gauntlet-style tiny synthetic inputs

        if outcome is None:
            continue

        regime_runs[policy_hash]["total"] += 1
        bucket = synthetic if is_synthetic else real
        bucket["total"] += 1

        qc = outcome.get("qc") or {}
        if qc.get("terminal_state") == "BLOCKED":
            regime_runs[policy_hash]["blocked"] += 1
            bucket["blocked"] += 1
        elif qc.get("terminal_state") == "SHIP":
            regime_runs[policy_hash]["ship"] += 1

        for ev in run:
            if ev["event_type"] == "factory.qc.verdict":
                day = str(ev.get("ts", ""))[:10]
                if ev.get("tier") == "critic":
                    critic_by_day[day].append(ev.get("latency_ms", 0))
                if ev.get("verdict") == "FAIL":
                    for v in ev.get("violations", []):
                        fam = family_of(str(v.get("constraint_id", "?")))
                        families[policy_hash][fam] += 1

    def pct(part: int, whole: int) -> str:
        return f"{(100.0 * part / whole):.1f}%" if whole else "n/a"

    def p95(values: list[float]) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]

    return {
        "runs_total": sum(r["total"] for r in regime_runs.values()),
        "by_policy_hash": {
            ph: {
                "runs": stats["total"],
                "ship": stats["ship"],
                "blocked": stats["blocked"],
                "blocked_rate": pct(stats["blocked"], stats["total"]),
                "fail_families": dict(sorted(families[ph].items(), key=lambda kv: -kv[1])),
            }
            for ph, stats in sorted(regime_runs.items())
        },
        "traffic": {
            "synthetic": {**synthetic, "blocked_rate": pct(synthetic["blocked"], synthetic["total"])},
            "real": {**real, "blocked_rate": pct(real["blocked"], real["total"])},
        },
        "critic_p95_by_day": {day: p95(vals) for day, vals in sorted(critic_by_day.items())},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=Path("report/factory_telemetry"))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    report = build_report(load_events(args.dir))
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
