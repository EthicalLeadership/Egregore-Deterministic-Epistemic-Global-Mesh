#!/usr/bin/env python3
"""Factory histogram bucketer — Phase 1 measurement deliverable.

Reads factory telemetry JSONL (report/factory_telemetry/*.jsonl), groups
events by run_id, assigns each run to a bucket, and writes
report/factory_histogram.json.

Usage:
    .venv/bin/python scripts/factory_histogram.py [--dir PATH] [--out PATH]
    .venv/bin/python scripts/factory_histogram.py --diff week1.json week2.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

# Bucket thresholds — tune after real traffic, not before.
BUCKETS: dict[str, dict[str, Any]] = {
    "trivial": {"max_stations": 1, "max_total_tokens": 500},
    "micro_solvable": {"max_stations": 2, "max_total_tokens": 2000, "max_station_ms": 5000},
    # structured_final = full pipeline, all stations ok, no m-failure
    # heavy = anything exceeding the limits below OR any m-failure
}
HEAVY_STATION_MS = 30_000
HEAVY_TOTAL_TOKENS = 8_000


def _load_events(directory: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in sorted(directory.glob("factory_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _group_runs(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Assemble per-run records from the four event types."""
    runs: dict[str, dict[str, Any]] = {}
    for ev in events:
        run_id = ev.get("run_id")
        if not run_id:
            continue
        run = runs.setdefault(
            run_id, {"run_id": run_id, "mode": ev.get("mode"), "stations": [], "inferences": []}
        )
        et = ev.get("event_type")
        if et == "factory.envelope.in":
            run["envelope"] = ev
        elif et == "factory.station":
            run["stations"].append(ev)
        elif et == "factory.inference":
            run["inferences"].append(ev)
        elif et == "factory.run.outcome":
            run["outcome"] = ev
    return runs


def _bucket(run: dict[str, Any]) -> str:
    outcome = run.get("outcome", {})
    inferences = run.get("inferences", [])
    stations = run.get("stations", [])

    m_failed = any(not all(i.get(f"m{n}", True) for n in (1, 2, 3, 4)) for i in inferences)
    total_tokens = outcome.get("total_tokens") or sum(i.get("total_tokens", 0) for i in inferences)
    n_stations = len(outcome.get("stations_taken") or stations)
    max_station_ms = max((s.get("elapsed_ms", 0) for s in stations), default=0)

    if (
        m_failed
        or not outcome.get("ok", True)
        or max_station_ms > HEAVY_STATION_MS
        or total_tokens > HEAVY_TOTAL_TOKENS
    ):
        return "heavy"

    t = BUCKETS["trivial"]
    if n_stations <= t["max_stations"] and total_tokens <= t["max_total_tokens"]:
        return "trivial"

    m = BUCKETS["micro_solvable"]
    if (
        n_stations <= m["max_stations"]
        and total_tokens <= m["max_total_tokens"]
        and max_station_ms <= m["max_station_ms"]
    ):
        return "micro_solvable"

    return "structured_final"


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0.0, "p95": 0.0, "max": 0.0}
    ordered = sorted(values)
    p95_idx = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return {
        "p50": statistics.median(ordered),
        "p95": ordered[p95_idx],
        "max": ordered[-1],
    }


def build_histogram(directory: Path) -> dict[str, Any]:
    runs = _group_runs(_load_events(directory))
    buckets: dict[str, list[dict[str, Any]]] = {
        "trivial": [], "micro_solvable": [], "structured_final": [], "heavy": [],
    }
    for run in runs.values():
        if "outcome" not in run:
            continue  # incomplete run (crashed mid-pipeline); count separately
        buckets[_bucket(run)].append(run)

    def summarize(runs_in_bucket: list[dict[str, Any]]) -> dict[str, Any]:
        elapsed = [r["outcome"].get("total_elapsed_ms", 0) for r in runs_in_bucket]
        tokens = [r["outcome"].get("total_tokens", 0) for r in runs_in_bucket]
        per_model: dict[str, int] = {}
        per_station: dict[str, int] = {}
        for r in runs_in_bucket:
            for i in r["inferences"]:
                per_model[i.get("eg_model", "?")] = per_model.get(i.get("eg_model", "?"), 0) + 1
            for s in r["stations"]:
                per_station[s.get("station", "?")] = per_station.get(s.get("station", "?"), 0) + 1
        return {
            "count": len(runs_in_bucket),
            "elapsed_ms": _percentiles(elapsed),
            "total_tokens": _percentiles(tokens),
            "calls_per_model": per_model,
            "runs_per_station": per_station,
        }

    return {
        "source_dir": str(directory),
        "total_runs": sum(len(v) for v in buckets.values()),
        "incomplete_runs": sum(1 for r in runs.values() if "outcome" not in r),
        "buckets": {name: summarize(rs) for name, rs in buckets.items()},
    }


def diff_histograms(a_path: Path, b_path: Path) -> dict[str, Any]:
    a = json.loads(a_path.read_text(encoding="utf-8"))
    b = json.loads(b_path.read_text(encoding="utf-8"))
    delta: dict[str, Any] = {"a": str(a_path), "b": str(b_path), "buckets": {}}
    for name in ("trivial", "micro_solvable", "structured_final", "heavy"):
        ca = a["buckets"].get(name, {}).get("count", 0)
        cb = b["buckets"].get(name, {}).get("count", 0)
        delta["buckets"][name] = {"a": ca, "b": cb, "delta": cb - ca}
    return delta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=Path("report/factory_telemetry"))
    parser.add_argument("--out", type=Path, default=Path("report/factory_histogram.json"))
    parser.add_argument("--diff", nargs=2, type=Path, metavar=("A", "B"),
                        help="Diff two histogram JSON files instead of building one")
    args = parser.parse_args()

    if args.diff:
        result = diff_histograms(args.diff[0], args.diff[1])
        print(json.dumps(result, indent=2))
        return 0

    if not args.dir.exists():
        print(f"telemetry dir not found: {args.dir}", file=sys.stderr)
        return 1
    histogram = build_histogram(args.dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(histogram, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(histogram, indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
