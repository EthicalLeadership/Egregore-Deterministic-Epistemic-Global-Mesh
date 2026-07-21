"""
BLACKSTAR LAW: SQLite Anchor Store
Local anchor persistence for edge deployment. Same contract as PostgresAnchorStore.
"""

from __future__ import annotations

import ast
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from egregore.domain.anchor_record import AnchorRecord


class SQLiteAnchorStore:
    """SQLite store for public anchor records."""

    def __init__(self, db_path: str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_table()

    def _ensure_table(self) -> None:
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS anchor_records (
                    anchor_id TEXT PRIMARY KEY,
                    tier TEXT NOT NULL,
                    block_hash TEXT NOT NULL,
                    notarization TEXT NOT NULL,
                    public_verify INTEGER NOT NULL,
                    timestamp_ns INTEGER NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
                """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_anchor_block_hash ON anchor_records(block_hash)"
            )
            conn.commit()

    def append(self, record: AnchorRecord) -> None:
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO anchor_records
                (anchor_id, tier, block_hash, notarization, public_verify, timestamp_ns, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.anchor_id,
                    record.tier,
                    record.block_hash,
                    record.notarization,
                    1 if record.public_verify else 0,
                    record.timestamp_ns,
                    str(dict(record.metadata)),
                ),
            )
            conn.commit()

    def get_by_block_hash(self, block_hash: str) -> AnchorRecord | None:
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM anchor_records WHERE block_hash = ? ORDER BY timestamp_ns DESC LIMIT 1",
                (block_hash,),
            ).fetchone()
            if row is None:
                return None
            return AnchorRecord(
                anchor_id=str(row["anchor_id"]),
                tier=str(row["tier"]),
                block_hash=str(row["block_hash"]),
                notarization=str(row["notarization"]),
                public_verify=bool(row["public_verify"]),
                timestamp_ns=int(row["timestamp_ns"]),
                metadata=ast.literal_eval(str(row["metadata"])),
            )

    def list_all(self) -> Sequence[AnchorRecord]:
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM anchor_records ORDER BY timestamp_ns ASC"
            ).fetchall()
            return tuple(
                AnchorRecord(
                    anchor_id=str(r["anchor_id"]),
                    tier=str(r["tier"]),
                    block_hash=str(r["block_hash"]),
                    notarization=str(r["notarization"]),
                    public_verify=bool(r["public_verify"]),
                    timestamp_ns=int(r["timestamp_ns"]),
                    metadata=ast.literal_eval(str(r["metadata"])),
                )
                for r in rows
            )
