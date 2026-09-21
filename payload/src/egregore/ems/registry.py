"""EMS Registry — SQLite-backed model catalog and lifecycle state.

Replaces Ollama's local model list with a sovereign, queryable registry.
Each node runs a Registry instance (or shares one via SQLite on NFS).
"""

from __future__ import annotations

import enum
import hashlib
import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from egregore.domain.provenance_model import ProvenanceEvent
from egregore.infrastructure.zarc_provenance_sink import ZarcProvenanceSink
from egregore.interface.provenance_port import IProvenanceSink
from egregore.kernel.provenance import Provenance


class ModelStatus(str, enum.Enum):
    STOPPED = "stopped"
    LOADING = "loading"
    RUNNING = "running"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ModelRecord:
    """Canonical representation of a registered model."""

    model_id: str
    version: str
    model_path: str
    backend_type: str = "native"  # native | (ext reserved)
    context_length: int = 8192
    parameters: str = "7B"
    tier: str = "general"  # expert | general | specialized
    status: ModelStatus = ModelStatus.STOPPED
    node: str = "pioneer1"
    host: str = "127.0.0.1"
    port: int = 0
    sha256: str = ""
    capabilities: str = "[]"  # JSON list
    chat_template: str = ""  # e.g. deepseek, qwen2, chatml, raw
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ModelRecord:
        d = dict(row)
        return cls(
            model_id=d["model_id"],
            version=d["version"],
            model_path=d["model_path"],
            backend_type=d.get("backend_type", "native"),
            context_length=d.get("context_length", 8192),
            parameters=d.get("parameters", "7B"),
            tier=d.get("tier", "general"),
            status=ModelStatus(d.get("status", "stopped")),
            node=d.get("node", "pioneer1"),
            host=d.get("host", "127.0.0.1"),
            port=d.get("port", 0),
            sha256=d.get("sha256", ""),
            capabilities=d.get("capabilities", "[]"),
            chat_template=d.get("chat_template", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )


DEFAULT_DB_PATH = Path(
    os.environ.get(
        "EGREGORE_EMS_DB",
        os.environ.get("EGREGORE_DATA_DIR", "~/egregore_data/pioneer1") + "/ems_registry.db",
    )
).expanduser()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS models (
    model_id      TEXT PRIMARY KEY,
    version       TEXT NOT NULL DEFAULT 'v1',
    model_path    TEXT NOT NULL,
    backend_type  TEXT NOT NULL DEFAULT 'native',
    context_length INTEGER NOT NULL DEFAULT 8192,
    parameters    TEXT NOT NULL DEFAULT '7B',
    tier          TEXT NOT NULL DEFAULT 'general',
    status        TEXT NOT NULL DEFAULT 'stopped'
                  CHECK(status IN ('stopped', 'loading', 'running', 'error')),
    node          TEXT NOT NULL DEFAULT 'pioneer1',
    host          TEXT NOT NULL DEFAULT '127.0.0.1',
    port          INTEGER NOT NULL DEFAULT 0,
    sha256        TEXT NOT NULL DEFAULT '',
    capabilities  TEXT NOT NULL DEFAULT '[]',
    chat_template TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT '',
    updated_at    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_models_status ON models(status);
CREATE INDEX IF NOT EXISTS idx_models_node   ON models(node);
CREATE INDEX IF NOT EXISTS idx_models_tier   ON models(tier);

CREATE TABLE IF NOT EXISTS promotions (
    promotion_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id       TEXT NOT NULL,
    checkpoints_dir TEXT NOT NULL,
    approval_sha256 TEXT NOT NULL,
    signature_sha256 TEXT NOT NULL,
    approved_by    TEXT NOT NULL,
    approved_at_ns INTEGER NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_promotions_model_id ON promotions(model_id);
"""


def _now() -> str:
    return str(int(time.time()))


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_path(path: Path) -> str:
    """Hash a file or a stable directory fingerprint (config.json)."""
    if path.is_file():
        return _sha256_file(str(path))
    # For HF checkpoints, hash config.json as a stable fingerprint.
    config = path / "config.json"
    if config.exists():
        return _sha256_file(str(config))
    return ""


def _read_text(path: Path, label: str) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing {label}: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Empty {label}: {path}")
    return text


class EmsRegistry:
    """SQLite-backed model registry with CRUD and discovery."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        provenance_sink: IProvenanceSink | None = None,
    ) -> None:
        self.db_path = Path(db_path).expanduser() if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._provenance_sink = provenance_sink or self._build_default_provenance_sink()
        self._init_schema()

    def _build_default_provenance_sink(self) -> IProvenanceSink | None:
        signing_key_hex = os.environ.get("EGREGORE_ZARC_SIGNING_KEY_HEX", "").strip()
        if not signing_key_hex:
            return None
        zarc_path = Path(
            os.environ.get(
                "EGREGORE_EMS_ZARC_PATH",
                str(self.db_path.with_suffix(".zarc")),
            )
        ).expanduser()
        provenance = Provenance(zarc_path=zarc_path, signing_key_hex=signing_key_hex)
        return ZarcProvenanceSink(provenance=provenance)

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(_SCHEMA)
            # Migration: legacy registries used gguf_path and tokenizer_type columns.
            # Rename gguf_path -> model_path and drop tokenizer_type if present.
            try:
                self._conn.execute("ALTER TABLE models RENAME COLUMN gguf_path TO model_path")
            except sqlite3.OperationalError:
                pass  # column already named model_path or table fresh
            try:
                self._conn.execute("ALTER TABLE models DROP COLUMN tokenizer_type")
            except sqlite3.OperationalError:
                pass
            try:
                self._conn.execute("ALTER TABLE models DROP COLUMN quantization")
            except sqlite3.OperationalError:
                pass
            try:
                self._conn.execute("ALTER TABLE models DROP COLUMN n_gpu_layers")
            except sqlite3.OperationalError:
                pass
            # Add new columns if migrating from older schema
            for col, ddl in (
                ("backend_type", "ALTER TABLE models ADD COLUMN backend_type TEXT NOT NULL DEFAULT 'native'"),
                ("chat_template", "ALTER TABLE models ADD COLUMN chat_template TEXT NOT NULL DEFAULT ''"),
                ("context_length", "ALTER TABLE models ADD COLUMN context_length INTEGER NOT NULL DEFAULT 8192"),
                ("parameters", "ALTER TABLE models ADD COLUMN parameters TEXT NOT NULL DEFAULT '7B'"),
                ("tier", "ALTER TABLE models ADD COLUMN tier TEXT NOT NULL DEFAULT 'general'"),
                ("sha256", "ALTER TABLE models ADD COLUMN sha256 TEXT NOT NULL DEFAULT ''"),
                ("capabilities", "ALTER TABLE models ADD COLUMN capabilities TEXT NOT NULL DEFAULT '[]'"),
                ("node", "ALTER TABLE models ADD COLUMN node TEXT NOT NULL DEFAULT 'pioneer1'"),
                ("host", "ALTER TABLE models ADD COLUMN host TEXT NOT NULL DEFAULT '127.0.0.1'"),
                ("port", "ALTER TABLE models ADD COLUMN port INTEGER NOT NULL DEFAULT 0"),
            ):
                try:
                    self._conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass

    def _begin_promotion_transaction(self) -> None:
        self._conn.execute("BEGIN IMMEDIATE")

    def _commit_promotion_transaction(self) -> None:
        self._conn.commit()

    def _rollback_promotion_transaction(self) -> None:
        self._conn.rollback()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def register(
        self,
        model_id: str,
        model_path: str,
        *,
        version: str = "v1",
        backend_type: str = "native",
        context_length: int = 8192,
        parameters: str = "7B",
        tier: str = "general",
        node: str = "pioneer1",
        host: str = "127.0.0.1",
        port: int = 0,
        capabilities: list[str] | None = None,
        chat_template: str = "",
        compute_hash: bool = True,
    ) -> ModelRecord:
        """Register a new model or update an existing one."""
        path = Path(model_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Model path not found: {path}")

        sha256 = _sha256_path(path) if compute_hash else ""
        caps = json.dumps(capabilities or [])
        now = _now()

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO models
                (model_id, version, model_path, backend_type, context_length,
                 parameters, tier, status, node, host, port,
                 sha256, capabilities, chat_template, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(model_id) DO UPDATE SET
                  version=excluded.version,
                  model_path=excluded.model_path,
                  backend_type=excluded.backend_type,
                  context_length=excluded.context_length,
                  parameters=excluded.parameters,
                  tier=excluded.tier,
                  node=excluded.node,
                  host=excluded.host,
                  port=excluded.port,
                  sha256=excluded.sha256,
                  capabilities=excluded.capabilities,
                  chat_template=excluded.chat_template,
                  updated_at=excluded.updated_at
                """,
                (
                    model_id,
                    version,
                    str(path),
                    backend_type,
                    context_length,
                    parameters,
                    tier,
                    ModelStatus.STOPPED.value,
                    node,
                    host,
                    port,
                    sha256,
                    caps,
                    chat_template,
                    now,
                    now,
                ),
            )
        rec = self.get(model_id)
        if rec is None:
            raise RuntimeError(f"Failed to register model '{model_id}'")
        assert rec is not None
        return rec

    def get(self, model_id: str) -> ModelRecord | None:
        cur = self._conn.execute(
            "SELECT * FROM models WHERE model_id = ?", (model_id,)
        )
        row = cur.fetchone()
        return ModelRecord.from_row(row) if row else None

    def list_models(
        self,
        *,
        status: ModelStatus | None = None,
        node: str | None = None,
        tier: str | None = None,
    ) -> list[ModelRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        if node:
            clauses.append("node = ?")
            params.append(node)
        if tier:
            clauses.append("tier = ?")
            params.append(tier)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        cur = self._conn.execute(f"SELECT * FROM models {where}", params)
        return [ModelRecord.from_row(r) for r in cur.fetchall()]

    def update_status(self, model_id: str, status: ModelStatus) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE models SET status = ?, updated_at = ? WHERE model_id = ?",
                (status.value, _now(), model_id),
            )

    def update_endpoint(self, model_id: str, host: str, port: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE models SET host = ?, port = ?, updated_at = ? WHERE model_id = ?",
                (host, port, _now(), model_id),
            )

    def delete(self, model_id: str) -> bool:
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM models WHERE model_id = ?", (model_id,)
            )
            return cur.rowcount > 0

    def verify_promotion_approval(
        self,
        *,
        model_id: str,
        checkpoints_dir: str,
        approval_artifact: str,
        approval_signature: str,
        approval_public_key: str,
    ) -> dict[str, Any]:
        artifact_path = Path(approval_artifact)
        signature_path = Path(approval_signature)
        public_key_path = Path(approval_public_key)
        checkpoints_path = Path(checkpoints_dir).expanduser().resolve()

        if not checkpoints_path.exists() or not checkpoints_path.is_dir():
            raise FileNotFoundError(f"Invalid checkpoints dir: {checkpoints_path}")

        artifact_bytes = artifact_path.read_bytes()
        if not artifact_bytes:
            raise ValueError(f"Empty approval artifact: {artifact_path}")
        try:
            doc = json.loads(artifact_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Approval artifact is not valid JSON: {exc}") from exc
        if not isinstance(doc, dict):
            raise ValueError("Approval artifact must be a JSON object")

        required = [
            "decision",
            "approved_by",
            "approved_at_ns",
            "model_family",
            "checkpoints_dir",
            "human_approval_required",
            "rationale",
        ]
        missing = [field for field in required if field not in doc]
        if missing:
            raise ValueError(
                f"Approval artifact missing required fields: {', '.join(missing)}"
            )

        if str(doc["decision"]).upper() != "APPROVED":
            raise ValueError("Approval decision is not APPROVED")
        if doc["human_approval_required"] is not True:
            raise ValueError("human_approval_required must be true")
        if str(doc["model_family"]).strip() != model_id:
            raise ValueError("Approval artifact model_family does not match model_id")
        declared_checkpoints = Path(str(doc["checkpoints_dir"])).expanduser().resolve()
        if declared_checkpoints != checkpoints_path:
            raise ValueError(
                "Approval artifact checkpoints_dir does not match promotion checkpoints_dir"
            )
        if not isinstance(doc["approved_at_ns"], int) or doc["approved_at_ns"] <= 0:
            raise ValueError("approved_at_ns must be a positive integer")
        if not str(doc["approved_by"]).strip():
            raise ValueError("approved_by must be non-empty")
        if not str(doc["rationale"]).strip():
            raise ValueError("rationale must be non-empty")

        signature_hex = _read_text(signature_path, "approval signature")
        public_key_hex = _read_text(public_key_path, "approval public key")
        signature = bytes.fromhex(signature_hex)
        verify_key = VerifyKey(bytes.fromhex(public_key_hex))
        try:
            verify_key.verify(artifact_bytes, signature)
        except BadSignatureError as exc:
            raise ValueError("Approval signature verification failed") from exc

        return {
            "model_id": model_id,
            "checkpoints_dir": str(checkpoints_path),
            "approval_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
            "signature_sha256": hashlib.sha256(signature).hexdigest(),
            "approved_by": str(doc["approved_by"]).strip(),
            "approved_at_ns": int(doc["approved_at_ns"]),
        }

    def promote(
        self,
        *,
        model_id: str,
        checkpoints_dir: str,
        approval_artifact: str,
        approval_signature: str,
        approval_public_key: str,
    ) -> dict[str, Any]:
        if self._provenance_sink is None:
            raise RuntimeError(
                "Promotion provenance sink unavailable; refusing mutable-only audit trail"
            )
        approval = self.verify_promotion_approval(
            model_id=model_id,
            checkpoints_dir=checkpoints_dir,
            approval_artifact=approval_artifact,
            approval_signature=approval_signature,
            approval_public_key=approval_public_key,
        )
        event_payload = {
            "model_id": approval["model_id"],
            "checkpoints_dir": approval["checkpoints_dir"],
            "approval_sha256": approval["approval_sha256"],
            "signature_sha256": approval["signature_sha256"],
            "approved_by": approval["approved_by"],
            "approved_at_ns": approval["approved_at_ns"],
        }
        now = _now()
        approval_emitted = False
        self._begin_promotion_transaction()
        try:
            self._conn.execute(
                """
                INSERT INTO promotions
                (model_id, checkpoints_dir, approval_sha256, signature_sha256,
                 approved_by, approved_at_ns, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval["model_id"],
                    approval["checkpoints_dir"],
                    approval["approval_sha256"],
                    approval["signature_sha256"],
                    approval["approved_by"],
                    approval["approved_at_ns"],
                    now,
                ),
            )
            self._provenance_sink.append(
                ProvenanceEvent(
                    engine="ems",
                    event="model_promotion_approved",
                    payload=event_payload,
                    ts_ns=time.time_ns(),
                )
            )
            approval_emitted = True
            self._commit_promotion_transaction()
        except Exception as exc:
            try:
                self._rollback_promotion_transaction()
            except Exception:
                pass

            if not approval_emitted:
                raise

            if isinstance(exc, (RuntimeError, ValueError, FileNotFoundError, BadSignatureError)):
                raise

            compensation_payload = {
                "model_id": approval["model_id"],
                "checkpoints_dir": approval["checkpoints_dir"],
                "approval_sha256": approval["approval_sha256"],
                "signature_sha256": approval["signature_sha256"],
                "reverted_by": "ems_registry",
                "reason": "sqlite_commit_failed_after_provenance_emit",
                "error": type(exc).__name__,
            }
            try:
                self._provenance_sink.append(
                    ProvenanceEvent(
                        engine="ems",
                        event="model_promotion_reverted",
                        payload=compensation_payload,
                        ts_ns=time.time_ns(),
                    )
                )
            except Exception as compensation_exc:
                raise RuntimeError(
                    "Promotion persistence failed after provenance emission; "
                    "compensation emit also failed"
                ) from compensation_exc
            raise RuntimeError(
                "Promotion persistence failed after provenance emission; "
                "compensation event emitted"
            ) from exc
        return approval

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def scan_model_directory(
        self,
        root: Path | str,
        *,
        node: str = "pioneer1",
        tier: str = "general",
    ) -> list[ModelRecord]:
        """Scan a directory tree for model checkpoints and auto-register them."""
        root = Path(root).expanduser()
        registered: list[ModelRecord] = []
        for path in root.rglob("*"):
            if not path.is_dir():
                continue
            if not (path / "config.json").exists():
                continue
            try:
                rec = self.register(
                    model_id=path.name,
                    model_path=str(path),
                    node=node,
                    tier=tier,
                )
                registered.append(rec)
            except Exception:
                continue
        return registered

    def verify_all(self) -> dict[str, str]:
        """Re-check SHA256 for every registered model."""
        results: dict[str, str] = {}
        for rec in self.list_models():
            path = Path(rec.model_path)
            if not path.exists():
                results[rec.model_id] = "MISSING"
                self.update_status(rec.model_id, ModelStatus.ERROR)
                continue
            actual = _sha256_path(path)
            if actual == rec.sha256:
                results[rec.model_id] = "VERIFIED"
            else:
                results[rec.model_id] = "CORRUPT"
                self.update_status(rec.model_id, ModelStatus.ERROR)
        return results

    def health(self) -> dict[str, Any]:
        total = len(self.list_models())
        running = len(self.list_models(status=ModelStatus.RUNNING))
        error = len(self.list_models(status=ModelStatus.ERROR))
        return {
            "status": "HEALTHY" if error == 0 else "DEGRADED",
            "total_models": total,
            "running": running,
            "error": error,
            "db_path": str(self.db_path),
        }


def build_registry_from_env() -> EmsRegistry:
    """Factory: create registry from environment."""
    db_path = os.environ.get("EGREGORE_EMS_DB")
    registry = EmsRegistry(db_path=db_path)

    # Auto-scan configured model roots
    model_roots = os.environ.get("EGREGORE_MODEL_ROOT", "/opt/egregore/models")
    node_id = os.environ.get("EGREGORE_NODE_ID", "pioneer1")
    for tier in ("expert", "general", "specialized"):
        tier_path = Path(model_roots) / tier
        if tier_path.exists():
            registry.scan_model_directory(tier_path, node=node_id, tier=tier)
    return registry
