from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CausalVector:
    """Causal metadata linking a block to its execution trace and distributed context."""

    trace_id: str = ""
    span_id: str = ""
    parent_span_id: str = ""
    vector: dict[str, int] = field(default_factory=dict)
    distributed: bool = False
    cross_node: bool = False


def generate_block_id(
    block_seq: int = 0,
    merkle_root: str = "",
    previous_block_hash: str = "",
    timestamp_ns: int = 0,
) -> str:
    """Deterministic block ID from canonical block inputs."""
    data = f"{block_seq}|{merkle_root}|{previous_block_hash}|{timestamp_ns}"
    return hashlib.sha256(data.encode()).hexdigest()


@dataclass
class ExecutionBlock:
    """Canonical SEL-X execution block.

    Supports two construction styles:
      - Legacy/simple: ``ExecutionBlock(tasks=..., dependencies=..., description=...,
        previous_block_hash=..., merkle_root=..., record_count=...)``
      - SEL-X: ``ExecutionBlock(block_id=..., block_seq=..., created_at_ns=...,
        records=..., merkle_root=..., previous_block_hash=..., causal_vector=...)``
    """

    # SEL-X identity / causality
    block_id: str = ""
    tenant_id: str = ""
    block_seq: int = 0
    block_height: int = 0
    previous_block_hash: str = "0" * 64
    merkle_root: str = ""
    record_count: int = 0
    records: Sequence[Any] = field(default_factory=tuple)
    causal_vector: CausalVector | None = None

    # Integrity / provenance
    block_hash: str = ""
    block_signature: str = ""
    integrity_hash: str | None = None
    created_at_ns: int = 0
    created_at: int = 0

    # Legacy simple-block fields
    tasks: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    description: str = ""

    @property
    def canonical_payload(self) -> bytes:
        """Canonical bytes used to compute the integrity hash."""
        record_hashes = [str(getattr(r, "integrity_hash", r)) for r in self.records]
        data = (
            f"{self.block_id}|{self.block_seq}|{self.previous_block_hash}|"
            f"{self.merkle_root}|{self.record_count}|{','.join(record_hashes)}"
        )
        return data.encode("utf-8")

    def __post_init__(self) -> None:
        if self.causal_vector is None:
            self.causal_vector = CausalVector()
        if self.created_at_ns == 0 and self.created_at != 0:
            self.created_at_ns = self.created_at
        if self.record_count == 0 and self.records:
            self.record_count = len(self.records)
        if self.block_height == 0 and self.block_seq != 0:
            self.block_height = self.block_seq
        if not self.block_id:
            self.block_id = str(uuid.uuid4())

    def generate_hash(self) -> str:
        """Legacy hash over the simple-block fields."""
        data = (
            f"{self.tasks}|{self.dependencies}|{self.description}|"
            f"{self.previous_block_hash}|{self.merkle_root}|{self.record_count}"
        )
        return hashlib.sha256(data.encode()).hexdigest()

    @staticmethod
    def compute_block_hash(
        block_id: str,
        tenant_id: str,
        block_height: int,
        previous_block_hash: str,
        merkle_root: str,
        record_count: int,
    ) -> str:
        """Deterministic block hash from canonical fields."""
        data = (
            f"{block_id}|{tenant_id}|{block_height}|{previous_block_hash}|"
            f"{merkle_root}|{record_count}"
        )
        return hashlib.sha256(data.encode()).hexdigest()

    def with_integrity_hash(self) -> ExecutionBlock:
        """Return a copy of this block with ``integrity_hash`` computed."""
        h = hashlib.sha256(self.canonical_payload).hexdigest()
        return ExecutionBlock(**{**self.__dict__, "integrity_hash": h})
