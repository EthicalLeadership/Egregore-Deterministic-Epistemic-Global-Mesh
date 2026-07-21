"""PostgreSQL-backed persistent store for SEL-X execution blocks."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

from egregore.domain.execution_block import ExecutionBlock


class PostgresBlockStore:
    """Append-only PostgreSQL store for ExecutionBlock."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._ensure_table()

    def _ensure_table(self) -> None:
        migration_path = (
            Path(__file__).resolve().parents[2]
            / "scripts"
            / "migrations"
            / "V001__selx_blocks.sql"
        )
        if migration_path.exists():
            sql = migration_path.read_text()
        else:
            sql = self._default_schema()
        with psycopg2.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()

    @staticmethod
    def _default_schema() -> str:
        return """
        CREATE TABLE IF NOT EXISTS execution_blocks (
            block_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            block_seq BIGINT NOT NULL,
            block_height BIGINT NOT NULL DEFAULT 0,
            previous_block_hash TEXT NOT NULL,
            merkle_root TEXT,
            record_count INTEGER NOT NULL,
            block_hash TEXT NOT NULL,
            block_signature TEXT,
            causal_vector JSONB NOT NULL DEFAULT '{}',
            records JSONB NOT NULL DEFAULT '[]',
            created_at_ns BIGINT NOT NULL
        );
        ALTER TABLE execution_blocks
            ADD COLUMN IF NOT EXISTS block_height BIGINT NOT NULL DEFAULT 0;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_blocks_tenant_seq
            ON execution_blocks(tenant_id, block_seq);
        CREATE INDEX IF NOT EXISTS idx_blocks_latest
            ON execution_blocks(tenant_id, block_seq DESC);
        CREATE INDEX IF NOT EXISTS idx_blocks_tenant_height
            ON execution_blocks(tenant_id, block_height DESC);
        """

    def append(self, block: ExecutionBlock) -> None:
        """Persist a block idempotently."""
        row = self._block_to_row(block)
        with psycopg2.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO execution_blocks
                    (block_id, tenant_id, block_seq, block_height, previous_block_hash, merkle_root,
                     record_count, block_hash, block_signature, causal_vector, records, created_at_ns)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (block_id) DO NOTHING
                    """,
                (
                    row["block_id"],
                    row["tenant_id"],
                    row["block_seq"],
                    row["block_height"],
                    row["previous_block_hash"],
                    row["merkle_root"],
                    row["record_count"],
                    row["block_hash"],
                    row["block_signature"],
                    Json(row["causal_vector"]),
                    Json(row["records"]),
                    row["created_at_ns"],
                ),
            )
            conn.commit()

    def get_latest_block_hash(self, tenant_id: str) -> str | None:
        with psycopg2.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT block_hash FROM execution_blocks WHERE tenant_id = %s ORDER BY block_seq DESC LIMIT 1",
                (tenant_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None

    def get_latest_height(self, tenant_id: str) -> int:
        with psycopg2.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT block_seq FROM execution_blocks WHERE tenant_id = %s ORDER BY block_seq DESC LIMIT 1",
                (tenant_id,),
            )
            row = cur.fetchone()
            return row[0] if row else -1

    def list_blocks(
        self, tenant_id: str, limit: int = 1000
    ) -> Sequence[ExecutionBlock]:
        with psycopg2.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT block_id, tenant_id, block_seq, block_height, previous_block_hash, merkle_root,
                           record_count, block_hash, block_signature, causal_vector, records, created_at_ns
                    FROM execution_blocks
                    WHERE tenant_id = %s
                    ORDER BY block_height ASC, block_seq ASC
                    LIMIT %s
                    """,
                (tenant_id, limit),
            )
            rows = cur.fetchall()
            return tuple(
                self._row_to_block(
                    dict(
                        zip(
                            [
                                "block_id",
                                "tenant_id",
                                "block_seq",
                                "block_height",
                                "previous_block_hash",
                                "merkle_root",
                                "record_count",
                                "block_hash",
                                "block_signature",
                                "causal_vector",
                                "records",
                                "created_at_ns",
                            ],
                            row,
                            strict=False,
                        )
                    )
                )
                for row in rows
            )

    def get_latest(self, tenant_id: str) -> ExecutionBlock | None:
        with psycopg2.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT block_id, tenant_id, block_seq, block_height, previous_block_hash, merkle_root,
                           record_count, block_hash, block_signature, causal_vector, records, created_at_ns
                    FROM execution_blocks
                    WHERE tenant_id = %s
                    ORDER BY block_height DESC, block_seq DESC
                    LIMIT 1
                    """,
                (tenant_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_block(
                dict(
                    zip(
                        [
                            "block_id",
                            "tenant_id",
                            "block_seq",
                            "block_height",
                            "previous_block_hash",
                            "merkle_root",
                            "record_count",
                            "block_hash",
                            "block_signature",
                            "causal_vector",
                            "records",
                            "created_at_ns",
                        ],
                        row,
                        strict=False,
                    )
                )
            )

    def get_by_height(self, tenant_id: str, height: int) -> ExecutionBlock | None:
        with psycopg2.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT block_id, tenant_id, block_seq, block_height, previous_block_hash, merkle_root,
                           record_count, block_hash, block_signature, causal_vector, records, created_at_ns
                    FROM execution_blocks
                    WHERE tenant_id = %s AND block_height = %s
                    ORDER BY block_seq DESC
                    LIMIT 1
                    """,
                (tenant_id, height),
            )
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_block(
                dict(
                    zip(
                        [
                            "block_id",
                            "tenant_id",
                            "block_seq",
                            "block_height",
                            "previous_block_hash",
                            "merkle_root",
                            "record_count",
                            "block_hash",
                            "block_signature",
                            "causal_vector",
                            "records",
                            "created_at_ns",
                        ],
                        row,
                        strict=False,
                    )
                )
            )

    def read_all(self, tenant_id: str) -> Sequence[ExecutionBlock]:
        with psycopg2.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT block_id, tenant_id, block_seq, block_height, previous_block_hash, merkle_root,
                           record_count, block_hash, block_signature, causal_vector, records, created_at_ns
                    FROM execution_blocks
                    WHERE tenant_id = %s
                    ORDER BY block_height ASC, block_seq ASC
                    """,
                (tenant_id,),
            )
            rows = cur.fetchall()
            return tuple(
                self._row_to_block(
                    dict(
                        zip(
                            [
                                "block_id",
                                "tenant_id",
                                "block_seq",
                                "block_height",
                                "previous_block_hash",
                                "merkle_root",
                                "record_count",
                                "block_hash",
                                "block_signature",
                                "causal_vector",
                                "records",
                                "created_at_ns",
                            ],
                            row,
                            strict=False,
                        )
                    )
                )
                for row in rows
            )

    @staticmethod
    def _block_to_row(block: ExecutionBlock) -> dict:
        return {
            "block_id": block.block_id,
            "tenant_id": block.records[0].tenant_id if block.records else "default",
            "block_seq": block.block_seq,
            "block_height": block.block_height or block.block_seq,
            "previous_block_hash": block.previous_block_hash,
            "merkle_root": block.merkle_root,
            "record_count": len(block.records),
            "block_hash": block.integrity_hash or "",
            "block_signature": "",  # reserved for future block-level signing
            "causal_vector": asdict(block.causal_vector),
            "records": [asdict(r) for r in block.records],
            "created_at_ns": block.created_at_ns,
        }

    @staticmethod
    def _row_to_block(row: dict) -> ExecutionBlock:
        from egregore.domain.execution_block import CausalVector
        from egregore.domain.execution_record import (
            BudgetContext,
            ExecutionRecord,
            PolicyContext,
        )

        records = []
        for r in row["records"]:
            policy = PolicyContext(**r.pop("policy_context"))
            budget_raw = r.pop("budget_context", None)
            budget = BudgetContext(**budget_raw) if budget_raw else None
            records.append(
                ExecutionRecord(policy_context=policy, budget_context=budget, **r)
            )

        causal_vector = CausalVector(**row["causal_vector"])
        return ExecutionBlock(
            block_id=row["block_id"],
            block_seq=row["block_seq"],
            block_height=row.get("block_height", row["block_seq"]),
            created_at_ns=row["created_at_ns"],
            records=tuple(records),
            merkle_root=row["merkle_root"],
            previous_block_hash=row["previous_block_hash"],
            causal_vector=causal_vector,
            integrity_hash=row["block_hash"],
        )
