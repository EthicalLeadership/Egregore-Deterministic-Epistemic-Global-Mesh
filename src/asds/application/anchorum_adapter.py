"""Filesystem-backed adapter implementing IAnchorumIngestionPort.

This is a pragmatic first implementation: ASDS does not yet have a dedicated
entity store, so the adapter bridges to the local filesystem using immutable
JSON records. It still satisfies the port contract — ANCHORUM code never opens
a file directly.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from asds.application.utils import canonical_json, hash_bytes, hash_file, now_iso
from asds.domain.models import Evidence, OutputVersion
from asds.domain.ports import IAnchorumIngestionPort

logger = logging.getLogger(__name__)

DEFAULT_ASDS_DATA_DIR = Path("/opt/egregore/asds_data")


class AnchorumAdapter(IAnchorumIngestionPort):
    """Concrete port adapter backed by immutable JSON files on disk."""

    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.data_dir = Path(
            data_dir or os.environ.get("ASDS_DATA_DIR", DEFAULT_ASDS_DATA_DIR)
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _workspace_dir(self, workspace_id: str) -> Path:
        path = self.data_dir / "workspaces" / workspace_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _evidence_dir(self, workspace_id: str) -> Path:
        path = self._workspace_dir(workspace_id) / "evidence"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _outputs_dir(self, workspace_id: str) -> Path:
        path = self._workspace_dir(workspace_id) / "outputs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _evidence_path(self, workspace_id: str, evidence_id: str) -> Path:
        return self._evidence_dir(workspace_id) / f"{evidence_id}.json"

    def _load_evidence_record(self, evidence_id: str) -> dict[str, Any] | None:
        """Search all workspaces for an evidence record by id."""
        workspaces = self.data_dir / "workspaces"
        if not workspaces.exists():
            return None
        for ws_dir in workspaces.iterdir():
            candidate = ws_dir / "evidence" / f"{evidence_id}.json"
            if candidate.exists():
                return json.loads(candidate.read_text(encoding="utf-8"))
        return None

    def _resolve_content_ref(self, content_ref: str) -> Path:
        """content_ref may be an absolute path or a file:// URI."""
        if content_ref.startswith("file://"):
            content_ref = content_ref[7:]
        path = Path(content_ref)
        if not path.exists():
            raise FileNotFoundError(f"Evidence content not found: {content_ref}")
        return path

    # ------------------------------------------------------------------
    # Port implementation
    # ------------------------------------------------------------------
    def _store_inline_content(
        self, workspace_id: str, content: bytes, filename_hint: str
    ) -> str:
        """Persist inline content bytes and return a file:// content_ref."""
        content_hash = hash_bytes(content)
        blob_dir = self._workspace_dir(workspace_id) / "blobs"
        blob_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(filename_hint).suffix if filename_hint else ".bin"
        blob_path = blob_dir / f"{content_hash[:32]}{ext}"
        blob_path.write_bytes(content)
        return f"file://{blob_path.resolve()}"

    def create_evidence(
        self,
        workspace_id: str,
        content_ref: str,
        case_id: str | None = None,
        content: bytes | None = None,
    ) -> Evidence:
        if content is not None:
            data = content
            content_hash = hash_bytes(data)
            size_bytes = len(data)
            original_filename = Path(content_ref).name if content_ref else "inline.bin"
            content_ref = self._store_inline_content(
                workspace_id, content, original_filename
            )
        else:
            path = self._resolve_content_ref(content_ref)
            data = path.read_bytes()
            content_hash = hash_file(path)
            size_bytes = path.stat().st_size
            original_filename = path.name

        # Minimal container/mime inference — enough for the port record.
        header = data[:8]
        if header.startswith(b"%PDF-"):
            container_type, mime_type = "pdf", "application/pdf"
        elif header.startswith(b"PK\x03\x04"):
            container_type, mime_type = "ooxml", "application/vnd.openxmlformats"
        elif header.startswith(b"\xd0\xcf\x11\xe0"):
            container_type, mime_type = "legacy_office", "application/msword"
        elif header.startswith(b"From ") or header.startswith(b"Return-Path:"):
            container_type, mime_type = "email", "message/rfc822"
        elif header.startswith(b"\xff\xd8\xff"):
            container_type, mime_type = "jpeg", "image/jpeg"
        elif header.startswith(b"\x89PNG"):
            container_type, mime_type = "png", "image/png"
        else:
            container_type, mime_type = "unknown", "application/octet-stream"

        evidence_id = content_hash[:32]
        evidence = Evidence(
            id=evidence_id,
            workspace_id=workspace_id,
            case_id=case_id,
            content_ref=content_ref,
            content_hash=content_hash,
            container_type=container_type,
            mime_type=mime_type,
            size_bytes=size_bytes,
            original_filename=original_filename,
        )

        record = {
            "id": evidence.id,
            "workspace_id": evidence.workspace_id,
            "case_id": evidence.case_id,
            "content_ref": evidence.content_ref,
            "content_hash": evidence.content_hash,
            "container_type": evidence.container_type,
            "mime_type": evidence.mime_type,
            "size_bytes": evidence.size_bytes,
            "original_filename": evidence.original_filename,
            "created_at": now_iso(),
        }
        evidence_path = self._evidence_path(workspace_id, evidence_id)
        evidence_path.write_text(canonical_json(record), encoding="utf-8")
        logger.info("Created evidence %s in workspace %s", evidence_id, workspace_id)
        return evidence

    def get_evidence_content(self, evidence_id: str) -> bytes:
        record = self._load_evidence_record(evidence_id)
        if record is None:
            raise KeyError(f"Evidence not found: {evidence_id}")
        path = self._resolve_content_ref(record["content_ref"])
        return path.read_bytes()

    def get_case_evidence(self, case_id: str) -> list[Evidence]:
        results: list[Evidence] = []
        workspaces = self.data_dir / "workspaces"
        if not workspaces.exists():
            return results
        for ws_dir in workspaces.iterdir():
            evidence_dir = ws_dir / "evidence"
            if not evidence_dir.exists():
                continue
            for record_path in evidence_dir.glob("*.json"):
                record = json.loads(record_path.read_text(encoding="utf-8"))
                if record.get("case_id") == case_id:
                    results.append(Evidence(**record))
        return results

    def register_analysis_output(
        self,
        evidence_id: str,
        analysis_type: str,
        result_json: dict[str, Any],
        content_hash: str,
    ) -> OutputVersion:
        record = self._load_evidence_record(evidence_id)
        if record is None:
            raise KeyError(f"Evidence not found: {evidence_id}")

        workspace_id = record["workspace_id"]
        outputs_dir = self._outputs_dir(workspace_id) / evidence_id / analysis_type
        outputs_dir.mkdir(parents=True, exist_ok=True)

        # Monotonic version number based on existing files.
        existing = sorted(outputs_dir.glob("*.json"))
        version_number = len(existing) + 1

        output_id = str(uuid.uuid4())
        output_path = outputs_dir / f"v{version_number:04d}.json"
        output_path.write_text(canonical_json(result_json), encoding="utf-8")

        output = OutputVersion(
            id=output_id,
            evidence_id=evidence_id,
            analysis_type=analysis_type,
            version_number=version_number,
            content_hash=content_hash,
            content_ref=f"file://{output_path.resolve()}",
        )

        # Persist lightweight output record for retrieval.
        record_path = outputs_dir / f"v{version_number:04d}.meta.json"
        record_path.write_text(
            canonical_json(
                {
                    "id": output.id,
                    "evidence_id": output.evidence_id,
                    "analysis_type": output.analysis_type,
                    "version_number": output.version_number,
                    "content_hash": output.content_hash,
                    "content_ref": output.content_ref,
                    "created_at": now_iso(),
                }
            ),
            encoding="utf-8",
        )

        logger.info(
            "Registered %s output v%s for evidence %s",
            analysis_type,
            version_number,
            evidence_id,
        )
        return output

    def emit_bus_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Emit a Plane-1 bus event.

        The current bus layer is a JetStream bootstrap helper only, so this
        implementation logs the event and appends it to a local event journal.
        When a live broker is available, this method becomes the publish call.
        """
        event = {"event_type": event_type, "payload": payload, "emitted_at": now_iso()}
        journal_path = self.data_dir / "bus_events.jsonl"
        with open(journal_path, "a", encoding="utf-8") as f:
            f.write(canonical_json(event) + "\n")
        logger.debug("Bus event emitted: %s", event_type)
