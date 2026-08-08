"""Anchor orchestrator service logic.

Polls the block store, submits block integrity hashes to a timestamp authority,
persists public anchor records, and falls back to locally signed tokens when the
TSA is unreachable.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from egregore.domain.anchor_record import AnchorRecord
from egregore.infrastructure.block_store import BlockStore
from egregore.services.anchor_orchestrator.timestamp_client import (
    ITimestampClient,
    LocalFallbackTimestampClient,
    RFC3161TimestampClient,
    TimestampError,
)
from egregore.shared.freeze_state import FreezeController


class AnchorOrchestrator:
    """Service that anchors execution blocks to a public timestamp authority."""

    def __init__(
        self,
        *,
        block_store: BlockStore,
        anchor_store: object,
        timestamp_client: ITimestampClient | None = None,
        tier: str = "tsa",
        freeze_controller: FreezeController | None = None,
    ) -> None:
        self._block_store = block_store
        self._anchor_store = anchor_store
        self._timestamp_client = timestamp_client
        self._tier = tier
        self._freeze_controller = freeze_controller

    @classmethod
    def from_dsn(
        cls,
        block_store_path: Path,
        dsn: str,
        *,
        tsa_url: str | None = None,
        signing_key_hex: str | None = None,
        tier: str = "tsa",
    ) -> AnchorOrchestrator:
        """Factory that wires PostgreSQL stores and a TSA client with fallback."""
        from egregore.infrastructure.postgres_block_store import PostgresBlockStore

        block_store: BlockStore = PostgresBlockStore(dsn)
        from egregore.infrastructure.postgres_anchor_store import PostgresAnchorStore

        anchor_store = PostgresAnchorStore(dsn)

        if tsa_url and signing_key_hex:
            fallback = LocalFallbackTimestampClient(signing_key_hex)
            timestamp_client: ITimestampClient = RFC3161TimestampClient(
                tsa_url, fallback=fallback
            )
        elif signing_key_hex:
            timestamp_client = LocalFallbackTimestampClient(signing_key_hex)
        else:
            from egregore.services.anchor_orchestrator.timestamp_client import (
                MockTimestampClient,
            )

            timestamp_client = MockTimestampClient()

        return cls(
            block_store=block_store,
            anchor_store=anchor_store,
            timestamp_client=timestamp_client,
            tier=tier,
        )

    def anchor_block(self, block_hash: str) -> AnchorRecord:
        """Anchor a single block hash and return the anchor record."""
        if self._timestamp_client is None:
            raise RuntimeError("No timestamp client configured")

        anchor_id = self._derive_anchor_id(block_hash)
        if self._anchor_store.get_by_block_hash(block_hash) is not None:
            raise RuntimeError(f"Block already anchored: {block_hash}")

        try:
            response = self._timestamp_client.timestamp(block_hash)
        except TimestampError as exc:
            if self._freeze_controller is not None:
                self._freeze_controller.fork_detected(
                    reason=f"Timestamp/anchor failure: {exc}",
                    timestamp_ns=time.time_ns(),
                    detection_source="anchor_orchestrator",
                )
            raise
        # Map TimestampToken (cms_bytes, timestamp_iso, tier) to AnchorRecord.
        try:
            ts_dt = datetime.fromisoformat(response.timestamp_iso)
            timestamp_ns = int(ts_dt.timestamp() * 1_000_000_000)
        except Exception:
            timestamp_ns = time.time_ns()

        source = "rfc3161" if response.tier >= 2 else "local_fallback"
        tsa_report = None
        if response.verification is not None:
            tsa_report = response.verification.to_canonical()
        record = AnchorRecord(
            anchor_id=anchor_id,
            tier=str(response.tier),
            block_hash=block_hash,
            notarization=response.cms_bytes.hex(),
            public_verify=response.verified,
            timestamp_ns=timestamp_ns,
            metadata={
                "source": source,
                "tier": str(response.tier),
                "verified": response.verified,
                "tsa_report": tsa_report,
            },
        )
        self._anchor_store.append(record)
        return record

    def anchor_unanchored_blocks(self, tenant_id: str) -> Iterable[AnchorRecord]:
        """Poll block store and anchor any blocks not yet anchored."""
        for block in self._block_store.read_all(tenant_id):
            block_hash = block.integrity_hash
            if block_hash is None:
                continue
            if self._anchor_store.get_by_block_hash(block_hash) is not None:
                continue
            yield self.anchor_block(block_hash)

    @staticmethod
    def _derive_anchor_id(block_hash: str) -> str:
        material = f"anchor:{block_hash}"
        return hashlib.sha256(material.encode()).hexdigest()
