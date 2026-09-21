"""Package pipeline outputs: JSON artifacts and signed .zarc bundles."""

from __future__ import annotations

import importlib
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from egregore.tooling.pipeline_models import AuditReport, ModuleManifest

MODULE_AUDITED_EVENT = "module_audited"
GRAPH_AUDITED_EVENT = "graph_audited"


def _load_signer_module() -> Any:
    return importlib.import_module("egregore.kernel.ed25519_signer")


def _load_provenance_module() -> Any:
    return importlib.import_module("egregore.kernel.provenance")


def _load_provenance_model() -> Any:
    return importlib.import_module("egregore.domain.provenance_model")


def _load_zarc_sink() -> Any:
    return importlib.import_module("egregore.infrastructure.zarc_provenance_sink")


def resolve_signing_key(signing_key_hex: str | None) -> str:
    """Return a signing key, falling back to env or a generated test key."""
    key = signing_key_hex or os.environ.get("EGREGORE_SIGNING_KEY_HEX")
    if key:
        return key
    signer = _load_signer_module()
    return signer.generate_signing_key()


def module_output_dir(out_dir: Path, module_id: str) -> Path:
    """Return the per-module output directory."""
    safe_id = module_id.replace(".", os.sep)
    path = out_dir / safe_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_module_artifacts(
    out_dir: Path,
    module_id: str,
    manifest: ModuleManifest,
    report: AuditReport,
) -> tuple[Path, Path]:
    """Write human-readable manifest and report JSON files."""
    target = module_output_dir(out_dir, module_id)
    manifest_path = target / "egregore-module.json"
    report_path = target / "audit_report.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return manifest_path, report_path


def write_zarc_bundle(
    zarc_path: Path,
    signing_key_hex: str,
    engine: str,
    event: str,
    payload: Mapping[str, Any],
    timestamp_ns: int | None = None,
) -> str:
    """Append a signed entry to a .zarc file and return the entry hash."""
    ts = timestamp_ns or time.time_ns()
    provenance_mod = _load_provenance_module()
    provenance_model = _load_provenance_model()
    sink_mod = _load_zarc_sink()

    provenance = provenance_mod.Provenance(
        zarc_path,
        signing_key_hex=signing_key_hex,
        prev_hash_init=None,
    )
    sink = sink_mod.ZarcProvenanceSink(provenance=provenance)
    sink.append(
        provenance_model.ProvenanceEvent(
            engine=engine,
            event=event,
            payload=payload,
            ts_ns=ts,
        )
    )
    return provenance._prev_hash


def package_module(
    out_dir: Path,
    module_id: str,
    manifest: ModuleManifest,
    report: AuditReport,
    signing_key_hex: str | None = None,
    timestamp_ns: int | None = None,
) -> tuple[Path, Path, Path]:
    """Write manifest, report, and signed .zarc bundle for a module audit."""
    manifest_path, report_path = write_module_artifacts(
        out_dir, module_id, manifest, report
    )
    key = resolve_signing_key(signing_key_hex)
    zarc_path = module_output_dir(out_dir, module_id) / "bundle.zarc"
    payload = {
        "module_id": module_id,
        "manifest": manifest.model_dump(mode="json"),
        "report": report.model_dump(mode="json"),
        "manifest_path": str(manifest_path),
        "report_path": str(report_path),
    }
    write_zarc_bundle(
        zarc_path=zarc_path,
        signing_key_hex=key,
        engine="module_pipeline",
        event=MODULE_AUDITED_EVENT,
        payload=payload,
        timestamp_ns=timestamp_ns,
    )
    return manifest_path, report_path, zarc_path
