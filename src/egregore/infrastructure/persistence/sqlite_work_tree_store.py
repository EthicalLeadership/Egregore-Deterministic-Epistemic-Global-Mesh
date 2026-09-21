"""SQLite-backed IWorkTreeStore."""

from __future__ import annotations

import base64
import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path

from egregore.domain.units import DT, TU
from egregore.domain.work_tree import WorkTree, WorkTreeNode
from egregore.domain.work_unit import WorkUnit, WorkUnitDemand, WorkUnitState, WorkUnitType
from egregore.shared.canonical import canonical_dumps, canonical_loads

from egregore.infrastructure.persistence.sqlite_job_store import default_runtime_db_path


class SQLiteWorkTreeStore:
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

    # -- serialization ---------------------------------------------------------

    @staticmethod
    def _work_unit_to_dict(wu: WorkUnit) -> dict:
        return {
            "work_unit_id": wu.work_unit_id,
            "work_unit_type": wu.work_unit_type.name,
            "demand": {
                "dt": wu.demand.dt.to_canonical(),
                "tu": wu.demand.tu.to_canonical(),
                "priority": wu.demand.priority,
                "max_wait_ms": wu.demand.max_wait_ms,
            },
            "payload_b64": base64.b64encode(wu.payload).decode("ascii"),
            "metadata": wu.metadata,
            "state": wu.state.name,
        }

    @staticmethod
    def _work_unit_from_dict(data: dict) -> WorkUnit:
        return WorkUnit(
            work_unit_id=data["work_unit_id"],
            work_unit_type=WorkUnitType[data["work_unit_type"]],
            demand=WorkUnitDemand(
                dt=DT.from_canonical(data["demand"]["dt"]),
                tu=TU.from_canonical(data["demand"]["tu"]),
                priority=data["demand"]["priority"],
                max_wait_ms=data["demand"]["max_wait_ms"],
            ),
            payload=base64.b64decode(data["payload_b64"]),
            metadata=data.get("metadata", {}),
            state=WorkUnitState[data["state"]],
        )

    @staticmethod
    def _tree_to_dict(tree: WorkTree) -> dict:
        nodes = []
        for node in tree.nodes.values():
            nodes.append(
                {
                    "node_id": node.node_id,
                    "tree_id": node.tree_id,
                    "path": node.path,
                    "parent_id": node.parent_id,
                    "work_unit": SQLiteWorkTreeStore._work_unit_to_dict(node.work_unit),
                    "child_ids": list(node.child_ids),
                    "state": node.state.name,
                    "created_at_ns": node.created_at_ns,
                }
            )
        return {
            "tree_id": tree.tree_id,
            "schema_version": tree.schema_version,
            "root_id": tree.root_id,
            "nodes": nodes,
        }

    @staticmethod
    def _tree_from_dict(data: dict) -> WorkTree:
        nodes = {}
        for n in data["nodes"]:
            node = WorkTreeNode(
                node_id=n["node_id"],
                tree_id=n["tree_id"],
                path=n["path"],
                parent_id=n["parent_id"],
                work_unit=SQLiteWorkTreeStore._work_unit_from_dict(n["work_unit"]),
                child_ids=tuple(n["child_ids"]),
                state=WorkUnitState[n["state"]],
                created_at_ns=n["created_at_ns"],
            )
            nodes[node.node_id] = node
        return WorkTree(
            tree_id=data["tree_id"],
            schema_version=data["schema_version"],
            root_id=data["root_id"],
            nodes=nodes,
        )

    # -- IWorkTreeStore methods -------------------------------------------------

    def save(self, tree: WorkTree) -> None:
        c = self._conn()
        tree_json = canonical_dumps(self._tree_to_dict(tree))
        c.execute(
            """
            INSERT INTO work_trees (tree_id, schema_version, root_id, tree_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(tree_id) DO UPDATE SET
                schema_version = excluded.schema_version,
                root_id = excluded.root_id,
                tree_json = excluded.tree_json,
                updated_at = excluded.updated_at
            """,
            (tree.tree_id, tree.schema_version, tree.root_id, tree_json, 0),
        )
        c.commit()

    def load(self, tree_id: str) -> WorkTree | None:
        row = self._conn().execute(
            "SELECT * FROM work_trees WHERE tree_id = ?", (tree_id,)
        ).fetchone()
        if row is None:
            return None
        return self._tree_from_dict(canonical_loads(row["tree_json"]))

    def list_tree_ids(self) -> Sequence[str]:
        rows = self._conn().execute("SELECT tree_id FROM work_trees").fetchall()
        return tuple(str(r["tree_id"]) for r in rows)
