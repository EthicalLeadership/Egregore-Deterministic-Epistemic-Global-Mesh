"""ANCHORUM filesystem-backed vault adapter.

Stores liberated artifacts under a deterministic directory tree with a JSON
manifest and SHA-256 verification. This is a Sprint 1 stub; later sprints may
add S3/MinIO adapters behind the same port.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VaultReceipt:
    artifact_id: str
    stored_path: Path
    sha256: str
    manifest_path: Path
    success: bool
    error_message: str | None = None


class FilesystemVaultAdapter:
    """Store forensic artifacts on local disk with deterministic IDs."""

    def __init__(self, vault_root: Path | str, node_id: str = "pioneer1") -> None:
        self.vault_root = Path(vault_root).expanduser()
        self.node_id = node_id
        self.artifacts_dir = self.vault_root / "artifacts"
        self.manifests_dir = self.vault_root / "manifests"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.manifests_dir.mkdir(parents=True, exist_ok=True)

    def store(
        self,
        source_path: Path | str,
        artifact_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> VaultReceipt:
        """Copy ``source_path`` into the vault and write a manifest."""
        source_path = Path(source_path)

        if not source_path.exists():
            return VaultReceipt(
                artifact_id=artifact_id or "unknown",
                stored_path=Path(),
                sha256="",
                manifest_path=Path(),
                success=False,
                error_message=f"Source path does not exist: {source_path}",
            )

        import hashlib

        h = hashlib.sha256()
        with open(source_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192 * 1024), b""):
                h.update(chunk)
        file_hash = h.hexdigest()

        artifact_id = artifact_id or file_hash[:16]
        dest_path = self.artifacts_dir / f"{artifact_id}.pdf"
        manifest_path = self.manifests_dir / f"{artifact_id}.json"

        shutil.copy2(source_path, dest_path)

        manifest = {
            "artifact_id": artifact_id,
            "node_id": self.node_id,
            "original_path": str(source_path),
            "stored_path": str(dest_path),
            "sha256": file_hash,
            "stored_at": datetime.now(UTC).isoformat(),
            "metadata": metadata or {},
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        return VaultReceipt(
            artifact_id=artifact_id,
            stored_path=dest_path,
            sha256=file_hash,
            manifest_path=manifest_path,
            success=True,
        )

    def retrieve(self, artifact_id: str) -> Path | None:
        """Return the stored artifact path if it exists."""
        candidate = self.artifacts_dir / f"{artifact_id}.pdf"
        return candidate if candidate.exists() else None
