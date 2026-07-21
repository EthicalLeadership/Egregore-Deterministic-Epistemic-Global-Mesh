from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from egregore.domain.execution_block import (
    CausalVector,
    ExecutionBlock,
    generate_block_id,
)
from egregore.domain.execution_record import ExecutionRecord
from egregore.shared.canonical import canonical_dumps
from egregore.shared.merkle import MerkleTree


@dataclass(frozen=True)
class BlockCommitPolicy:
    """Flush policy for block builder."""

    max_records: int = 100
    max_age_ns: int = 60_000_000_000  # 60 seconds
    flush_on_demand: bool = True


class ExecutionBlockBuilder:
    """Aggregates execution records into canonical SEL-X blocks."""

    def __init__(
        self,
        *,
        node_id: str = "default",
        commit_policy: BlockCommitPolicy | None = None,
        initial_previous_hash: str | None = None,
        now_ns: Callable[[], int] | None = None,
        signer: Callable[[str], str] | None = None,
    ) -> None:
        self._node_id = node_id
        self._commit_policy = commit_policy or BlockCommitPolicy()
        self._now_ns = now_ns or time.time_ns
        self._records: list[ExecutionRecord] = []
        self._block_seq = 0
        self._last_block_hash = initial_previous_hash or "0" * 64
        self._first_buffered_ns: int | None = None
        self._signer = signer or (lambda _hash: "")

    @property
    def pending_count(self) -> int:
        return len(self._records)

    @property
    def last_block_hash(self) -> str:
        return self._last_block_hash

    def append(self, record: ExecutionRecord) -> ExecutionBlock | None:
        """Add a record; return a block if the commit policy triggers."""
        self._records.append(record)
        if self._first_buffered_ns is None:
            self._first_buffered_ns = self._now_ns()

        if len(self._records) >= self._commit_policy.max_records:
            return self.flush()

        if self._first_buffered_ns is not None:
            age_ns = self._now_ns() - self._first_buffered_ns
            if age_ns >= self._commit_policy.max_age_ns:
                return self.flush()

        return None

    def flush(self) -> ExecutionBlock | None:
        """Build a block from pending records and reset the buffer."""
        if not self._records:
            return None

        records = self._records
        self._records = []
        self._first_buffered_ns = None

        # Build Merkle tree over canonical record bytes.
        leaves = [
            canonical_dumps(record.__dict__, default=str).encode("utf-8")
            for record in records
        ]
        tree = MerkleTree(leaves)
        merkle_root = tree.root_hash

        # Block timestamp is the latest record timestamp, or now if empty.
        timestamp_ns = max((r.timestamp_ns for r in records), default=self._now_ns())
        block_id = generate_block_id(
            block_seq=self._block_seq,
            merkle_root=merkle_root,
            previous_block_hash=self._last_block_hash,
            timestamp_ns=timestamp_ns,
        )

        # Simple causal vector: parent_span_id from first record, distributed False.
        causal_vector = CausalVector(
            parent_span_id=records[0].trace_id if records else "",
            distributed=False,
            cross_node=False,
        )

        block = ExecutionBlock(
            block_id=block_id,
            tenant_id=records[0].tenant_id if records else "",
            block_seq=self._block_seq,
            block_height=self._block_seq,
            created_at_ns=timestamp_ns,
            records=tuple(records),
            merkle_root=merkle_root,
            previous_block_hash=self._last_block_hash,
            causal_vector=causal_vector,
        ).with_integrity_hash()

        # Sign the block if a signer was configured.
        block.block_signature = self._signer(block.integrity_hash)

        self._block_seq += 1
        self._last_block_hash = block.integrity_hash or self._last_block_hash

        return block

    def build_all(self, records: Sequence[ExecutionRecord]) -> list[ExecutionBlock]:
        """Build blocks from a complete sequence, respecting max_records."""
        blocks: list[ExecutionBlock] = []
        for record in records:
            block = self.append(record)
            if block is not None:
                blocks.append(block)
        final = self.flush()
        if final is not None:
            blocks.append(final)
        return blocks
