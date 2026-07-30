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


class EmsRegistry:
    """SQLite-backed model registry with CRUD and discovery."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path).expanduser() if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

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
        return self.get(model_id)

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
