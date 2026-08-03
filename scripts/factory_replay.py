#!/usr/bin/env python3
"""Factory replay harness — Phase 7 determinism verdict.

Executes a factory workload TWICE through the live line (QC gate and policy
fully in the loop), then byte-compares outputs and canonical telemetry traces
with volatile fields stripped. Ported from the DFIH DiffReport pattern
(identical + mismatches); the DFIH state machine is deliberately not ported.

Usage:
    .venv/bin/python scripts/factory_replay.py --case MOLSON-2026
    .venv/bin/python scripts/factory_replay.py --case GDC-86849-02 --mode case_report
    .venv/bin/python scripts/factory_replay.py --input-file x.txt --mode general_assistant

Verdict:
    DETERMINISTIC — identical final_output AND identical stripped traces
    DIVERGED      — any difference, with evidence recorded
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

# Fields that legitimately differ between two runs of identical input.
VOLATILE_FIELDS = {
    "ts", "ts_ns", "seq_no", "run_id", "latency_ms", "elapsed_ms",
    "total_elapsed_ms", "vram_free_mb", "inference_id",
}

STRIP_PREFIX = "[QC FLAGGED] "


def load_case_input(case_id: str) -> str:
    """Build the bounded case digest (same shape as the ANCHORUM chat context)."""
    from egregore.interface.anchorum_http import _case_context

    return _case_context(case_id)


def run_factory(
    *, base_url: str, api_key: str, mode: str, input_text: str, max_tokens: int
) -> dict[str, Any]:
    r = requests.post(
        f"{base_url}/api/v1/factory/{mode}",
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        json={"input": input_text, "max_tokens": max_tokens, "temperature": 0.0},
        timeout=280,
    )
    r.raise_for_status()
    return r.json()


def read_run_trace(telemetry_dir: Path, run_id: str, after_ns: int) -> list[dict[str, Any]]:
    """Read one run's telemetry events recorded after a marker timestamp."""
    events: list[dict[str, Any]] = []
    for path in sorted(telemetry_dir.glob("factory_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("run_id") == run_id and ev.get("ts_ns", 0) >= after_ns:
                events.append(ev)
    return events


def strip_volatile(event: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in event.items() if k not in VOLATILE_FIELDS}


def compare_runs(
    resp_a: dict[str, Any],
    resp_b: dict[str, Any],
    trace_a: list[dict[str, Any]],
    trace_b: list[dict[str, Any]],
) -> dict[str, Any]:
    """DFIH-style DiffReport over outputs + stripped traces."""
    out_a = resp_a.get("final_output", "")
    out_b = resp_b.get("final_output", "")
    output_identical = out_a == out_b

    sa = [strip_volatile(e) for e in trace_a]
    sb = [strip_volatile(e) for e in trace_b]
    trace_diffs: list[dict[str, Any]] = []
    if len(sa) != len(sb):
        trace_diffs.append({"kind": "event_count", "a": len(sa), "b": len(sb)})
    for i, (ea, eb) in enumerate(zip(sa, sb)):
        if ea != eb:
            keys = {k for k in set(ea) | set(eb) if ea.get(k) != eb.get(k)}
            trace_diffs.append(
                {
                    "kind": "payload",
                    "index": i,
                    "event_type": ea.get("event_type") or eb.get("event_type"),
                    "differing_keys": sorted(keys),
                }
            )
            if len(trace_diffs) >= 10:
                break

    deterministic = output_identical and not trace_diffs
    return {
        "deterministic": deterministic,
        "output_identical": output_identical,
        "trace_diffs": trace_diffs,
    }


def _run_ids_from_response(telemetry_dir: Path, since_ns: int) -> list[str]:
    ids: list[str] = []
    for path in sorted(telemetry_dir.glob("factory_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("event_type") == "factory.envelope.in" and ev.get("ts_ns", 0) >= since_ns:
                ids.append(ev["run_id"])
    return ids


def station_summary(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for ev in trace:
        if ev["event_type"] == "factory.station":
            out.append(
                {
                    "station": ev.get("station"),
                    "elapsed_ms": ev.get("elapsed_ms"),
                    "tokens": ev.get("tokens"),
                    "backend": ev.get("backend"),
                }
            )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", help="ANCHORUM case id (input built from report digest)")
    parser.add_argument("--input-file", type=Path, help="raw input file instead of a case")
    parser.add_argument("--mode", default="case_report")
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--telemetry-dir", type=Path, default=Path("report/factory_telemetry"))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if args.case:
        input_text = load_case_input(args.case)
        label = args.case
    elif args.input_file:
        input_text = args.input_file.read_text(encoding="utf-8")
        label = args.input_file.stem
    else:
        parser.error("--case or --input-file required")

    api_key = Path("secrets/api_key.hex").read_text(encoding="utf-8").strip()
    out_path = args.out or Path(f"report/phase7_replay_{label.replace('/', '_')}.json")

    marker = time.time_ns()
    results: list[dict[str, Any]] = []
    for attempt in (1, 2):
        print(f"execution {attempt}/2 running…", flush=True)
        resp = run_factory(
            base_url=args.base_url,
            api_key=api_key,
            mode=args.mode,
            input_text=input_text,
            max_tokens=args.max_tokens,
        )
        results.append(resp)

    time.sleep(1)  # let the last telemetry lines flush
    run_ids = _run_ids_from_response(args.telemetry_dir, marker)[-2:]
    traces = [read_run_trace(args.telemetry_dir, rid, marker) for rid in run_ids]

    verdict = compare_runs(results[0], results[1], traces[0], traces[1])
    qc_a = (results[0].get("qc") or {}).get("terminal_state")
    qc_b = (results[1].get("qc") or {}).get("terminal_state")

    report = {
        "case": label,
        "mode": args.mode,
        **verdict,
        "qc_states": [qc_a, qc_b],
        "output_a_head": results[0].get("final_output", "")[:400],
        "output_b_head": results[1].get("final_output", "")[:400],
        "stations_a": station_summary(traces[0]),
        "stations_b": station_summary(traces[1]),
        "total_tokens": [
            (results[0].get("provenance") or {}).get("total_tokens"),
            (results[1].get("provenance") or {}).get("total_tokens"),
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")

    print(json.dumps({k: report[k] for k in ("case", "deterministic", "output_identical", "qc_states")}, indent=2))
    if verdict["trace_diffs"]:
        print(f"trace diffs: {len(verdict['trace_diffs'])} (see report)")
    print(f"wrote {out_path}")
    return 0 if verdict["deterministic"] else 2


if __name__ == "__main__":
    sys.exit(main())
