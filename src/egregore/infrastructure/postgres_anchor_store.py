"""PostgreSQL-backed persistent store for SEL-X anchor records."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

from egregore.domain.anchor_record import AnchorRecord


class PostgresAnchorStore:
    """PostgreSQL store for public anchor records."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._ensure_table()

    def _ensure_table(self) -> None:
        migration_path = (
            Path(__file__).resolve().parents[2]
            / "scripts"
            / "migrations"
            / "V002__selx_anchors.sql"
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
        CREATE TABLE IF NOT EXISTS anchor_records (
            anchor_id TEXT PRIMARY KEY,
            tier TEXT NOT NULL,
            block_hash TEXT NOT NULL,
            notarization TEXT NOT NULL,
            public_verify BOOLEAN NOT NULL,
            timestamp_ns BIGINT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_anchor_block_hash ON anchor_records(block_hash);
        """

    def append(self, record: AnchorRecord) -> None:
        with psycopg2.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO anchor_records
                    (anchor_id, tier, block_hash, notarization, public_verify, timestamp_ns, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (anchor_id) DO NOTHING
                    """,
                (
                    record.anchor_id,
                    record.tier,
                    record.block_hash,
                    record.notarization,
                    record.public_verify,
                    record.timestamp_ns,
                    Json(dict(record.metadata)),
                ),
            )
            conn.commit()

    def get_by_id(self, anchor_id: str) -> AnchorRecord | None:
        with psycopg2.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM anchor_records WHERE anchor_id = %s LIMIT 1",
                (anchor_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            cols = [desc[0] for desc in cur.description]
            data = dict(zip(cols, row, strict=False))
            return AnchorRecord(**data)

    def get_by_block_hash(self, block_hash: str) -> AnchorRecord | None:
        with psycopg2.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM anchor_records WHERE block_hash = %s ORDER BY timestamp_ns DESC LIMIT 1",
                (block_hash,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            cols = [desc[0] for desc in cur.description]
            data = dict(zip(cols, row, strict=False))
            return AnchorRecord(**data)

    def list_all(self) -> Sequence[AnchorRecord]:
        with psycopg2.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM anchor_records ORDER BY timestamp_ns ASC")
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]
            return tuple(
                AnchorRecord(**dict(zip(cols, row, strict=False))) for row in rows
            )
