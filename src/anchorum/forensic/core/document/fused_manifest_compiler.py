"""ANCHORUM Fused Manifest Compiler.

Fuses outputs from Planes 1-4 into a single court-ready ANCHORUM Document
Intelligence Record (ADIR).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anchorum.forensic.core.document.hidden_layer_detection import HiddenLayerVerdict
from anchorum.forensic.core.document.integrity_attestation import IntegrityAttestation
from anchorum.forensic.core.document.metadata_extraction import MetadataPlane
from anchorum.forensic.core.document.pdf_pharos_engine import DocumentVerdict
from anchorum.forensic.core.document.signature_pharos import SignatureVerdict


@dataclass(frozen=True)
class FusedManifest:
    anchorum_id: str
    input_path: Path
    created_at: str
    classification: DocumentVerdict
    metadata: MetadataPlane
    signature: SignatureVerdict
    hidden_layers: HiddenLayerVerdict
    integrity: IntegrityAttestation
    tags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchorum_id": self.anchorum_id,
            "input_path": str(self.input_path),
            "created_at": self.created_at,
            "classification": self.classification.to_dict(),
            "metadata": self.metadata.to_dict(),
            "signature": self.signature.to_dict(),
            "hidden_layers": self.hidden_layers.to_dict(),
            "integrity": self.integrity.to_dict(),
            "tags": self.tags,
            "notes": self.notes,
        }

    def write_json(self, output_path: Path | str) -> Path:
        path = Path(output_path)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path


def compile_fused_manifest(
    anchorum_id: str,
    input_path: Path | str,
    classification: DocumentVerdict,
    metadata: MetadataPlane,
    signature: SignatureVerdict,
    hidden_layers: HiddenLayerVerdict,
    integrity: IntegrityAttestation,
) -> FusedManifest:
    """Compile a single fused ADIR record from all forensic planes."""
    tags: list[str] = []
    notes: list[str] = []

    if classification.is_redacted:
        tags.append("redacted")
    if classification.is_scanned:
        tags.append("scanned")
    if signature.unsigned_count:
        tags.append("signature_issue")
    if hidden_layers.total_hidden_layers:
        tags.append("hidden_content")
    if not integrity.is_valid:
        tags.append("integrity_failure")

    if classification.classification_confidence < 0.5:
        notes.append("Low classification confidence; manual review recommended.")
    if signature.has_expired:
        notes.append("Document contains expired digital signatures.")
    if metadata.encrypted:
        notes.append("Document-level encryption was reported by parser.")

    return FusedManifest(
        anchorum_id=anchorum_id,
        input_path=Path(input_path),
        created_at=datetime.now(UTC).isoformat(),
        classification=classification,
        metadata=metadata,
        signature=signature,
        hidden_layers=hidden_layers,
        integrity=integrity,
        tags=sorted(set(tags)),
        notes=notes,
    )
