"""SQLite-backed IJobStore."""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from egregore.domain.job_models import (
    ComplexityTier,
    JobClassification,
    ResourceProfile,
)
from egregore.domain.scheduler_models import Job, PriorityTier, SLA, SLAClass
from egregore.shared.canonical import canonical_dumps, canonical_loads


def default_runtime_db_path() -> Path:
    node_id = os.environ.get("EGREGORE_NODE_ID", "pioneer1")
    data_dir = Path(os.environ.get("EGREGORE_DATA_DIR", f"~/egregore_data/{node_id}"))
    return data_dir.expanduser() / "node.db"


class SQLiteJobStore:
    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = Path(db_path) if db_path else default_runtime_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._ensure_migrated()

    def _conn(self) -> sqlite3.Connection:
        c: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(str(self._db_path), check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL;")
            c.execute("PRAGMA synchronous=NORMAL;")
            c.execute("PRAGMA foreign_keys=ON;")
            c.row_factory = sqlite3.Row
            self._local.conn = c
        return c

    def _ensure_migrated(self) -> None:
        from egregore.infrastructure.persistence.migrate import SQLITE_MIGRATIONS

        c = self._conn()
        c.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at INTEGER DEFAULT (unixepoch()))"
        )
        applied = {r[0] for r in c.execute("SELECT version FROM schema_migrations")}
        for version, name, fn in SQLITE_MIGRATIONS:
            if version not in applied:
                fn(c)
                c.execute(
                    "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (?, ?)",
                    (version, name),
                )
                c.commit()

    # -- serialization helpers -------------------------------------------------

    @staticmethod
    def _classification_to_dict(cls: JobClassification) -> dict[str, Any]:
        return {
            "__type__": "JobClassification",
            "job_id": cls.job_id,
            "complexity": cls.complexity.value,
            "resource_profile": {
                "cpu_percent": cls.resource_profile.cpu_percent,
                "memory_mb": cls.resource_profile.memory_mb,
                "vram_mb": cls.resource_profile.vram_mb,
                "disk_iops": cls.resource_profile.disk_iops,
                "network_mbps": cls.resource_profile.network_mbps,
            },
            "estimated_tokens": cls.estimated_tokens,
            "target_vertical": cls.target_vertical,
            "requested_capabilities": cls.requested_capabilities,
            "deterministic_required": cls.deterministic_required,
            "priority_tier": cls.priority_tier,
            "created_at_ns": cls.created_at_ns,
        }

    @staticmethod
    def _classification_from_dict(data: dict[str, Any]) -> JobClassification:
        rp = data.get("resource_profile", {})
        return JobClassification(
            job_id=data["job_id"],
            complexity=ComplexityTier(data["complexity"]),
            resource_profile=ResourceProfile(
                cpu_percent=rp.get("cpu_percent", 0.0),
                memory_mb=rp.get("memory_mb", 0),
                vram_mb=rp.get("vram_mb", 0),
                disk_iops=rp.get("disk_iops", 0),
                network_mbps=rp.get("network_mbps", 0),
            ),
            estimated_tokens=data.get("estimated_tokens", 0),
            target_vertical=data.get("target_vertical", ""),
            requested_capabilities=data.get("requested_capabilities", []),
            deterministic_required=data.get("deterministic_required", False),
            priority_tier=data.get("priority_tier", ""),
            created_at_ns=data.get("created_at_ns", 0),
        )

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        return Job(
            job_id=str(row["job_id"]),
            tenant_id=str(row["tenant_id"]),
            trace_id=str(row["trace_id"]),
            priority_tier=PriorityTier(str(row["priority_tier"])),
            sla=SLA(
                latency_target_ms=int(row["sla_latency_target_ms"]),
                throughput_target_qps=float(row["sla_throughput_target_qps"]),
                reliability_target=float(row["sla_reliability_target"]),
                class_=SLAClass(str(row["sla_class"])),
            ),
            classification=SQLiteJobStore._classification_from_dict(
                canonical_loads(row["classification_json"])
            ),
            status=str(row["status"]),
            created_at_ns=int(row["created_at_ns"]),
            scheduled_at_ns=int(row["scheduled_at_ns"]),
            started_at_ns=int(row["started_at_ns"]),
            completed_at_ns=int(row["completed_at_ns"]),
            assigned_node_id=str(row["assigned_node_id"]),
            metadata=canonical_loads(row["metadata_json"] or "{}"),
        )

    # -- IJobStore methods ----------------------------------------------------

    def insert(self, job: Job) -> bool:
        c = self._conn()
        try:
            c.execute(
                """
                INSERT INTO jobs (
                    job_id, tenant_id, trace_id, priority_tier,
                    sla_latency_target_ms, sla_throughput_target_qps,
                    sla_reliability_target, sla_class, classification_json,
                    status, created_at_ns, scheduled_at_ns, started_at_ns,
                    completed_at_ns, assigned_node_id, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.tenant_id,
                    job.trace_id,
                    job.priority_tier.value,
                    job.sla.latency_target_ms,
                    job.sla.throughput_target_qps,
                    job.sla.reliability_target,
                    job.sla.class_.value,
                    canonical_dumps(self._classification_to_dict(job.classification)),
                    job.status,
                    job.created_at_ns,
                    job.scheduled_at_ns,
                    job.started_at_ns,
                    job.completed_at_ns,
                    job.assigned_node_id,
                    canonical_dumps(job.metadata),
                ),
            )
            c.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def fetch_pending(self, tenant_id: str, limit: int) -> list[Job]:
        rows = (
            self._conn()
            .execute(
                "SELECT * FROM jobs WHERE tenant_id = ? AND status = 'PENDING' ORDER BY created_at_ns ASC LIMIT ?",
                (tenant_id, limit),
            )
            .fetchall()
        )
        return [self._row_to_job(r) for r in rows]

    def update_status(self, job_id: str, status: str, node_id: str = "") -> bool:
        c = self._conn()
        if node_id:
            cur = c.execute(
                "UPDATE jobs SET status = ?, assigned_node_id = ? WHERE job_id = ?",
                (status, node_id, job_id),
            )
        else:
            cur = c.execute(
                "UPDATE jobs SET status = ? WHERE job_id = ?",
                (status, job_id),
            )
        c.commit()
        return cur.rowcount > 0

    def count_by_status(self, tenant_id: str) -> dict[str, int]:
        rows = (
            self._conn()
            .execute(
                "SELECT status, COUNT(*) as cnt FROM jobs WHERE tenant_id = ? GROUP BY status",
                (tenant_id,),
            )
            .fetchall()
        )
        return {str(r["status"]): int(r["cnt"]) for r in rows}

    def oldest_pending(self, tenant_id: str) -> int:
        row = (
            self._conn()
            .execute(
                "SELECT MIN(created_at_ns) as oldest FROM jobs WHERE tenant_id = ? AND status = 'PENDING'",
                (tenant_id,),
            )
            .fetchone()
        )
        return int(row["oldest"] or 0)
