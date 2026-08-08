# epistemic marker: provenance / auditability
"""BCCBP Stage-Gate Controller.

Enforces the 8-stage artifact protocol for every cell in the University.
State is persisted in SQLite. Each stage unlocks only when its artifact exists
and passes validation.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

# PyYAML has no PEP 561 stubs; ignore for compatibility.
import yaml  # type: ignore[import-untyped]

from egregore.shared.canonical import canonical_dumps, canonical_loads

DB_PATH = (
    Path(os.environ.get("EGREGORE_REPO_ROOT", "/opt/egregore")) / "rag/cell_protocol.db"
)
STAGES = ["plan", "draw", "layout", "erect", "build", "finish", "inspect", "deliver"]


class StageStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class CellState:
    cell_id: str
    version: str
    taxonomy: str
    owner: str
    current_stage: str
    status: str  # overall status: active, failed, delivered
    stage_states: dict[str, dict[str, Any]]
    created_at: str
    updated_at: str


class CellProtocolController:
    """SQLite-backed controller for the BCCBP stage-gate machine."""

    def __init__(self, db_path: Path | str = DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cells (
                    cell_id TEXT PRIMARY KEY,
                    version TEXT NOT NULL,
                    taxonomy TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    current_stage TEXT NOT NULL DEFAULT 'plan',
                    status TEXT NOT NULL DEFAULT 'active',
                    stage_states TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stage_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cell_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    artifact_path TEXT,
                    checksum TEXT,
                    validator_output TEXT,
                    recorded_at TEXT NOT NULL
                )
                """)
            conn.commit()

    def now(self) -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _normalize_taxonomy(taxonomy: Any) -> str:
        """Accept either a slash-delimited string or a structured taxonomy dict."""
        if isinstance(taxonomy, str):
            return taxonomy
        if isinstance(taxonomy, dict):
            parts = [taxonomy.get("root"), taxonomy.get("branch"), taxonomy.get("leaf")]
            if taxonomy.get("specialty"):
                parts.append(taxonomy["specialty"])
            return "/".join(str(p) for p in parts if p)
        raise ValueError(f"taxonomy must be a string or dict, got {type(taxonomy)}")

    def register_cell(self, spec_path: Path | str) -> CellState:
        """Validate a cell spec and register it in the protocol."""
        spec_path = Path(spec_path)
        if not spec_path.exists():
            raise FileNotFoundError(f"Spec not found: {spec_path}")

        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        self._validate_spec(spec)

        cell_id = spec["cell_id"]
        version = spec["version"]
        taxonomy = self._normalize_taxonomy(spec["taxonomy"])
        owner = spec["owner"]

        stage_states = {
            stage: {
                "status": StageStatus.PENDING.value,
                "artifact_path": None,
                "checksum": None,
                "validator_output": None,
            }
            for stage in STAGES
        }
        spec_bytes = spec_path.read_bytes()
        stage_states["plan"]["status"] = StageStatus.COMPLETED.value
        stage_states["plan"]["artifact_path"] = str(spec_path.resolve())
        stage_states["plan"]["checksum"] = self._checksum(spec_bytes)
        stage_states["draw"]["status"] = StageStatus.IN_PROGRESS.value

        now = self.now()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO cells (cell_id, version, taxonomy, owner, current_stage, status, stage_states, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cell_id) DO UPDATE SET
                    version=excluded.version,
                    taxonomy=excluded.taxonomy,
                    owner=excluded.owner,
                    current_stage=excluded.current_stage,
                    status=excluded.status,
                    stage_states=excluded.stage_states,
                    updated_at=excluded.updated_at
                """,
                (
                    cell_id,
                    version,
                    taxonomy,
                    owner,
                    "draw",
                    "active",
                    canonical_dumps(stage_states),
                    now,
                    now,
                ),
            )
            conn.commit()

        return self.get_state(cell_id)

    def _validate_spec(self, spec: dict[str, Any]) -> None:
        """Minimal schema validation. Could be replaced with jsonschema."""
        required = [
            "cell_id",
            "version",
            "taxonomy",
            "owner",
            "purpose",
            "inputs",
            "outputs",
            "pipeline",
            "models",
            "verification",
            "moral_compliance",
            "dependencies",
            "artifacts",
        ]
        missing = [k for k in required if k not in spec]
        if missing:
            raise ValueError(f"Spec missing required fields: {missing}")

        # Validate pipeline stages are ordered and dependencies reference valid stages
        stage_ids = {s["stage_id"] for s in spec["pipeline"]["stages"]}
        for stage in spec["pipeline"]["stages"]:
            for dep in stage.get("depends_on", []):
                if dep not in stage_ids:
                    raise ValueError(
                        f"Stage '{stage['stage_id']}' depends on unknown stage '{dep}'"
                    )

        # Validate artifact stage gates
        gates = spec["artifacts"]["stage_gates"]
        for stage in STAGES:
            if stage not in gates:
                raise ValueError(f"Missing artifact path for stage '{stage}'")

    def submit_artifact(
        self,
        cell_id: str,
        stage: str,
        artifact_path: Path | str,
        validator_output: str | None = None,
    ) -> CellState:
        """Submit an artifact for a stage and advance if it passes."""
        if stage not in STAGES:
            raise ValueError(f"Unknown stage: {stage}")

        artifact_path = Path(artifact_path)
        if not artifact_path.exists():
            raise FileNotFoundError(f"Artifact not found: {artifact_path}")

        state = self.get_state(cell_id)
        stage_idx = STAGES.index(stage)
        if stage_idx > 0:
            prev_stage = STAGES[stage_idx - 1]
            prev_status = state.stage_states.get(prev_stage, {}).get("status")
            if prev_status != StageStatus.COMPLETED.value:
                raise PermissionError(
                    f"Stage '{stage}' is blocked. Previous stage '{prev_stage}' is '{prev_status}'."
                )

        current_status = state.stage_states.get(stage, {}).get("status")
        if current_status == StageStatus.COMPLETED.value:
            raise ValueError(f"Stage '{stage}' is already completed.")

        # Compute checksum
        content = artifact_path.read_bytes()
        checksum = self._checksum(content)

        # Determine pass/fail (simple: artifact exists and validator did not report FAIL)
        passed = True
        if validator_output and "FAIL" in validator_output.upper():
            passed = False

        new_status = StageStatus.COMPLETED.value if passed else StageStatus.FAILED.value
        stage_states = state.stage_states
        stage_states[stage]["status"] = new_status
        stage_states[stage]["artifact_path"] = str(artifact_path.resolve())
        stage_states[stage]["checksum"] = checksum
        stage_states[stage]["validator_output"] = validator_output

        current_stage = stage
        overall_status = state.status
        if passed:
            # Advance to next stage or mark delivered
            if stage == "deliver":
                current_stage = "deliver"
                overall_status = "delivered"
            else:
                next_stage = STAGES[stage_idx + 1]
                current_stage = next_stage
                if stage_states[next_stage]["status"] == StageStatus.PENDING.value:
                    stage_states[next_stage]["status"] = StageStatus.IN_PROGRESS.value
        else:
            overall_status = "failed"

        now = self.now()
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE cells
                SET current_stage = ?, status = ?, stage_states = ?, updated_at = ?
                WHERE cell_id = ?
                """,
                (
                    current_stage,
                    overall_status,
                    canonical_dumps(stage_states),
                    now,
                    cell_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO stage_history (cell_id, stage, status, artifact_path, checksum, validator_output, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cell_id,
                    stage,
                    new_status,
                    str(artifact_path.resolve()),
                    checksum,
                    validator_output,
                    now,
                ),
            )
            conn.commit()

        return self.get_state(cell_id)

    def _checksum(self, content: bytes) -> str:
        import hashlib

        return hashlib.sha256(content).hexdigest()

    def get_state(self, cell_id: str) -> CellState:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM cells WHERE cell_id = ?", (cell_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Cell not registered: {cell_id}")
        return CellState(
            cell_id=row["cell_id"],
            version=row["version"],
            taxonomy=row["taxonomy"],
            owner=row["owner"],
            current_stage=row["current_stage"],
            status=row["status"],
            stage_states=canonical_loads(row["stage_states"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_cells(self) -> list[CellState]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM cells ORDER BY updated_at DESC"
            ).fetchall()
        return [
            CellState(
                cell_id=r["cell_id"],
                version=r["version"],
                taxonomy=r["taxonomy"],
                owner=r["owner"],
                current_stage=r["current_stage"],
                status=r["status"],
                stage_states=canonical_loads(r["stage_states"]),
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def reset_cell(self, cell_id: str) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM cells WHERE cell_id = ?", (cell_id,))
            conn.execute("DELETE FROM stage_history WHERE cell_id = ?", (cell_id,))
            conn.commit()


def controller() -> CellProtocolController:
    """Return the singleton-ish protocol controller."""
    return CellProtocolController()
