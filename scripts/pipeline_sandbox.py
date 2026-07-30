#!/usr/bin/env python3
"""Pipeline sandbox — compulsory build-time gate for src/egregore/ modules.

Runs M1 (and M2 when a manifest exists) on every top-level module, produces
signed per-module bundles, and writes an aggregate signed provenance report.

Environment variables:
  EGREGORE_SIGNING_KEY_HEX  Ed25519 signing key (hex). Falls back to generated test key.
  EGREGORE_SANDBOX_OUT_DIR  Output directory (default: sandbox_outputs).
  EGREGORE_SANDBOX_STRICT   If set, fail any module without egregore-module.json.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from egregore.tooling.pipeline_packager import module_output_dir


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


# The sandbox root defaults to the repository containing this script, but can
# be overridden for testing or monorepo layouts.
REPO_ROOT = Path(
    _env("EGREGORE_SANDBOX_SRC_ROOT", str(Path(__file__).resolve().parents[1]))
).resolve()
SRC_ROOT = REPO_ROOT / "src"
PKG_ROOT = SRC_ROOT / "egregore"


def _signing_key() -> str:
    key = _env("EGREGORE_SIGNING_KEY_HEX", "")
    if key:
        return key
    # Fallback to a deterministic test key so CI never blocks on missing secrets.
    from egregore.kernel.ed25519_signer import generate_signing_key

    return generate_signing_key()


def _discover_modules() -> list[Path]:
    """Return top-level module directories under src/egregore/."""
    modules: list[Path] = []
    if not PKG_ROOT.exists():
        return modules
    for entry in sorted(PKG_ROOT.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if entry.name == "__pycache__":
            continue
        # Only consider Python files directly in the candidate module directory.
        # This avoids treating the parent ``egregore`` package itself as a module.
        if any(
            f.suffix == ".py" and not f.name.startswith("test_")
            for f in entry.iterdir()
            if f.is_file()
        ):
            modules.append(entry)
    return modules


def _load_manifest_from_output(out_dir: Path, module_id: str) -> dict[str, Any] | None:
    """Load the packaged manifest for a module, if it exists."""
    manifest_path = module_output_dir(out_dir, module_id) / "egregore-module.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _attestation_badge(manifest: dict[str, Any] | None) -> str:
    """Classify a module's M3 attestation posture for dashboard display."""
    if manifest is None:
        return "NOT_TERMINAL"
    cbi0 = manifest.get("cbi0", {})
    m3 = cbi0.get("m3", {})
    if not m3.get("terminal", False):
        return "NOT_TERMINAL"
    decom = m3.get("decom_manifest")
    if decom is None:
        return "MISSING"
    attestation = decom.get("attestation") or {}
    if (
        attestation.get("signature")
        and attestation.get("signer_id")
        and attestation.get("timestamp")
    ):
        return "SIGNED"
    if attestation.get("bootstrap_waiver"):
        return "WAIVER"
    return "MISSING"


def _aggregate_violations(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Aggregate violations from all checkpoints into a flat list."""
    aggregated: list[dict[str, Any]] = []
    for checkpoint in ("m1", "m2", "m3", "m5"):
        block = report.get(checkpoint, {}) or {}
        for violation in block.get("violations", []) or []:
            aggregated.append({"checkpoint": checkpoint.upper(), **violation})
    return aggregated


def _enrich_module_result(
    result: dict[str, Any], manifest: dict[str, Any] | None, report: dict[str, Any]
) -> dict[str, Any]:
    """Add display fields used by the Interface Synod dashboard."""
    module_id = result["module_id"]
    short_name = module_id.split(".")[-1] if "." in module_id else module_id
    cbi0 = manifest.get("cbi0", {}) if manifest else {}
    m3 = cbi0.get("m3", {}) if cbi0 else {}
    timestamp_ns = report.get("timestamp_ns")
    build_timestamp = (
        datetime.datetime.fromtimestamp(timestamp_ns / 1e9, tz=datetime.UTC).isoformat()
        if timestamp_ns
        else None
    )
    return {
        **result,
        "name": short_name,
        "layer": cbi0.get("m1_layer", "unknown"),
        "terminal": m3.get("terminal", False),
        "decom_manifest": m3.get("decom_manifest"),
        "attestation_badge": _attestation_badge(manifest),
        "violations": _aggregate_violations(report),
        "build_timestamp": build_timestamp,
    }


def _run_check(
    module_dir: Path, out_dir: Path, pipeline_class: str, signing_key: str
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        "-m",
        "egregore.tooling.module_pipeline",
        "check",
        "--module-dir",
        str(module_dir),
        "--class",
        pipeline_class,
        "--out-dir",
        str(out_dir),
        "--signing-key-hex",
        signing_key,
        "--src-root",
        str(SRC_ROOT),
    ]
    result = subprocess.run(  # noqa: S603
        cmd, cwd=REPO_ROOT, capture_output=True, text=True
    )
    report_path = (
        module_output_dir(out_dir, f"egregore.{module_dir.name}") / "audit_report.json"
    )
    report: dict[str, Any] = {}
    if report_path.exists():
        with contextlib.suppress(Exception):
            report = json.loads(report_path.read_text(encoding="utf-8"))
    module_id = report.get("module_id", f"egregore.{module_dir.name}")
    manifest = _load_manifest_from_output(out_dir, module_id)
    base_result = {
        "module_id": module_id,
        "pipeline_class": pipeline_class,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "m1": report.get("m1", {}),
        "m2": report.get("m2", {}),
        "m3": report.get("m3", {}),
        "m4": report.get("m4", {}),
        "m5": report.get("m5", {}),
    }
    return _enrich_module_result(base_result, manifest, report)


def _run_graph(
    module_dirs: list[Path], out_dir: Path, signing_key: str
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        "-m",
        "egregore.tooling.module_pipeline",
        "graph",
        "--modules",
        *[str(d) for d in module_dirs],
        "--out-dir",
        str(out_dir),
        "--signing-key-hex",
        signing_key,
        "--src-root",
        str(SRC_ROOT),
    ]
    result = subprocess.run(  # noqa: S603
        cmd, cwd=REPO_ROOT, capture_output=True, text=True
    )
    report_path = out_dir / "m2_graph_report.json"
    report: dict[str, Any] = {}
    if report_path.exists():
        with contextlib.suppress(Exception):
            report = json.loads(report_path.read_text(encoding="utf-8"))
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "report": report,
    }


def _write_aggregate_zarc(
    out_dir: Path, aggregate: dict[str, Any], signing_key: str
) -> Path:
    from egregore.domain.provenance_model import ProvenanceEvent
    from egregore.infrastructure.zarc_provenance_sink import ZarcProvenanceSink
    from egregore.kernel.provenance import Provenance

    zarc_path = out_dir / "sandbox.zarc"
    provenance = Provenance(zarc_path, signing_key_hex=signing_key, prev_hash_init=None)
    sink = ZarcProvenanceSink(provenance=provenance)
    sink.append(
        ProvenanceEvent(
            engine="pipeline_sandbox",
            event="sandbox_completed",
            payload=aggregate,
            ts_ns=aggregate["timestamp_ns"],
        )
    )
    return zarc_path


def main(argv: list[str] | None = None) -> int:
    out_dir = Path(_env("EGREGORE_SANDBOX_OUT_DIR", "sandbox_outputs")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    strict = bool(_env("EGREGORE_SANDBOX_STRICT", ""))
    signing_key = _signing_key()

    modules = _discover_modules()
    if not modules:
        print("No modules discovered under src/egregore/", file=sys.stderr)
        return 0

    module_results: list[dict[str, Any]] = []
    graph_modules: list[Path] = []
    failed = False

    for module_dir in modules:
        has_manifest = (module_dir / "egregore-module.json").exists()
        if strict and not has_manifest:
            print(
                f"FAIL {module_dir.name}: missing egregore-module.json (strict mode)",
                file=sys.stderr,
            )
            failed = True
            continue

        pipeline_class = "standard" if has_manifest else "fast"
        print(f"Checking {module_dir.name} ({pipeline_class}) ...")
        result = _run_check(module_dir, out_dir, pipeline_class, signing_key)
        module_results.append(result)

        if result["returncode"] != 0:
            print(f"  FAIL {result['module_id']}", file=sys.stderr)
            failed = True
        else:
            print(f"  PASS {result['module_id']}")

        if has_manifest:
            graph_modules.append(module_dir)

    graph_result: dict[str, Any] = {"status": "NOT_RUN", "returncode": 0}
    if graph_modules:
        print(f"Running M2 graph audit on {len(graph_modules)} module(s) ...")
        graph_result = _run_graph(graph_modules, out_dir, signing_key)
        if graph_result["returncode"] != 0:
            print("  FAIL M2 graph audit", file=sys.stderr)
            failed = True
        else:
            print("  PASS M2 graph audit")
    else:
        print("Skipping M2 graph audit: no modules have egregore-module.json")

    aggregate = {
        "timestamp_ns": int(time.time_ns()),
        "modules_scanned": len(modules),
        "modules_with_manifests": len(graph_modules),
        "failed": failed,
        "module_results": module_results,
        "graph": graph_result.get("report", graph_result),
    }

    aggregate_path = out_dir / "aggregate_report.json"
    aggregate_path.write_text(json.dumps(aggregate, indent=2), encoding="utf-8")

    zarc_path = _write_aggregate_zarc(out_dir, aggregate, signing_key)

    print(f"Wrote aggregate report: {aggregate_path}")
    print(f"Wrote sandbox provenance: {zarc_path}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
