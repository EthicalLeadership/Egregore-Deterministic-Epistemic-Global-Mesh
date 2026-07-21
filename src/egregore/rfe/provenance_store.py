"""Provenance persistence helpers for the Reproducible Fusion Engine.

This module lives in the ``egregore.rfe`` layer so that the HTTP router does
not need a direct dependency on ``egregore.kernel``.
"""

from __future__ import annotations

from typing import Any

from egregore.kernel.provenance import Provenance
from egregore.rfe.config import RFEConfig


def get_provenance_store(config: RFEConfig) -> Provenance:
    """Return a configured Provenance store for RFE events."""
    path = config.zarc_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return Provenance(
        path,
        signing_key_hex=config.signing_key_hex,
    )


def append_report_event(
    provenance: Provenance,
    *,
    case_id: str,
    version_id: str,
    report_hash: str,
    decision_log_hash: str,
    report: dict[str, Any],
    manifest_fingerprint: str,
    manifest: dict[str, Any] | None = None,
) -> None:
    """Append a signed ``report_generated`` event to the .zarc chain."""
    payload: dict[str, Any] = {
        "case_id": case_id,
        "version_id": version_id,
        "report_hash": report_hash,
        "decision_log_hash": decision_log_hash,
        "report": report,
        "manifest_fingerprint": manifest_fingerprint,
    }
    if manifest is not None:
        payload["manifest"] = manifest
    provenance.append(
        engine="rfe",
        event="report_generated",
        payload=payload,
    )


def get_manifest_by_hash(
    config: RFEConfig, manifest_hash: str
) -> dict[str, Any] | None:
    """Return a persisted manifest by its SHA-256 fingerprint, if available."""
    provenance = get_provenance_store(config)
    for entry in provenance.iter_entries():
        if entry.engine != "rfe" or entry.event != "report_generated":
            continue
        payload = entry.payload
        if payload.get("manifest_fingerprint") == manifest_hash:
            manifest = payload.get("manifest")
            if isinstance(manifest, dict):
                return dict(manifest)
    return None
