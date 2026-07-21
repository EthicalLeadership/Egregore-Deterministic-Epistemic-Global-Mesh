#!/usr/bin/env python3
import argparse
import contextlib
import json
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


def check_health(endpoint):
    try:
        with urllib.request.urlopen(  # noqa: S310
            endpoint + "/health", timeout=5
        ) as response:
            return response.status == 200
    except Exception:
        return False


def audit_performance():
    latencies = []
    for _ in range(10):
        start = time.perf_counter()
        with contextlib.suppress(BaseException):
            urllib.request.urlopen(  # noqa: S310
                "http://localhost:8002/health", timeout=5
            )
        latencies.append((time.perf_counter() - start) * 1000)
    s = sorted(latencies)
    n = len(s)
    return {
        "status": "PASS" if s[-1] < 1000 else "FAIL",
        "p50_ms": s[n // 2],
        "p95_ms": s[int(n * 0.95)],
    }


def audit_security():
    try:
        from egregore.governance import CBI0Governance

        g = CBI0Governance()
        ok = all(
            [hasattr(g, "m1"), hasattr(g, "m2"), hasattr(g, "m3"), hasattr(g, "m4")]
        )
        return {"status": "PASS" if ok else "FAIL", "cbi0": ok}
    except Exception as e:
        return {"status": "FAIL", "error": str(e)}


def audit_resilience():
    core = check_health("http://localhost:8002")
    return {"status": "PASS" if core else "FAIL", "core": core}


def audit_patterns():
    from pathlib import Path

    p = {
        "circuit_breaker": False,
        "retry": False,
        "timeout": False,
        "bulkhead": False,
        "fallback": False,
        "health_check": False,
    }
    for d in [Path("src/egregore"), Path("src/egregore/patterns")]:
        for f in d.rglob("*.py"):
            with contextlib.suppress(Exception):
                t = f.read_text()
                if "circuit" in t or "CircuitBreaker" in t:
                    p["circuit_breaker"] = True
                if "retry" in t or "backoff" in t:
                    p["retry"] = True
                if "timeout" in t or "Timeout" in t:
                    p["timeout"] = True
                if (
                    "bulkhead" in t
                    or "Bulkhead" in t
                    or "semaphore" in t
                    or "Semaphore" in t
                ):
                    p["bulkhead"] = True
                if "fallback" in t or "Fallback" in t:
                    p["fallback"] = True
                if "health" in t or "/health" in t:
                    p["health_check"] = True
    f = sum(1 for v in p.values() if v)
    return {"status": "PASS" if f >= 5 else "FAIL", "found": f, "patterns": p}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    r = {
        "audit_id": "QAV-" + str(time.time_ns()),
        "timestamp": datetime.now(UTC).isoformat(),
        "dimensions": {
            "performance": audit_performance(),
            "security": audit_security(),
            "resilience": audit_resilience(),
            "patterns": audit_patterns(),
        },
    }
    pc = sum(1 for v in r["dimensions"].values() if v.get("status") == "PASS")
    r["overall_score"] = pc / 4 * 100
    r["overall_pass"] = pc == 4
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(r, f, indent=2)
    print("QAV AUDIT: " + str(r["overall_score"]) + "/100")
    print("PASS: " + str(r["overall_pass"]))
    for k, v in r["dimensions"].items():
        print("  " + k + ": " + v["status"])


if __name__ == "__main__":
    main()
