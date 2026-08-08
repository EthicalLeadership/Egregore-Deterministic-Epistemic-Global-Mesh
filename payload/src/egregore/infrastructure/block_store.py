"""Append-only block store for ExecutionBlock persistence.

Uses a zarc-like JSONL format where each line is a canonical JSON block.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path

from egregore.domain.execution_block import ExecutionBlock
from egregore.shared.canonical import canonical_dumps, canonical_loads


class BlockStore:
    """Append-only store for execution blocks."""

    def __init__(self, store_path: Path) -> None:
        self._store_path = Path(store_path)
        self._store_path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, block: ExecutionBlock) -> None:
        """Append a block to the store.

        Records and the causal vector are serialized via ``asdict`` (not the
        lossy ``default=str`` repr path) so the chain remains independently
        verifiable from the persisted representation.
        """
        obj = dict(block.__dict__)
        obj["records"] = [
            asdict(r) if is_dataclass(r) else r for r in block.records
        ]
        obj["causal_vector"] = (
            asdict(block.causal_vector) if block.causal_vector is not None else None
        )
        line = canonical_dumps(obj, default=str)
        with self._store_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()

    def read_all(self) -> list[ExecutionBlock]:
        """Read all blocks from the store."""
        blocks: list[ExecutionBlock] = []
        if not self._store_path.exists():
            return blocks
        with self._store_path.open("r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                obj = canonical_loads(raw)
                blocks.append(ExecutionBlock(**obj))
        return blocks

    def last_block_hash(self) -> str:
        """Return the integrity hash of the last block, or genesis."""
        blocks = self.read_all()
        if not blocks:
            return "0" * 64
        return blocks[-1].integrity_hash or "0" * 64
