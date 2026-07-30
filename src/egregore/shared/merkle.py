# epistemic marker: provenance / auditability
"""Merkle tree utilities for SEL-X block integrity."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class InclusionProof:
    leaf_index: int
    leaf_hash: str
    sibling_hashes: tuple[str, ...]
    root_hash: str


def _hash_pair(left: str, right: str) -> str:
    """Deterministic parent hash from two child hashes."""
    # Sort to avoid order-dependent trees; this is a standard commutative hash.
    a, b = sorted((left, right))
    return hashlib.sha256(f"{a}{b}".encode()).hexdigest()


def _leaf_hash(data: bytes) -> str:
    """Hash a leaf with a domain separator."""
    return hashlib.sha256(b"\x00" + data).hexdigest()


class MerkleTree:
    """SHA-256 Merkle tree with inclusion proofs."""

    def __init__(self, leaves: Sequence[bytes]) -> None:
        self._leaves: list[bytes] = list(leaves)
        self._leaf_hashes: list[str] = [_leaf_hash(leaf) for leaf in self._leaves]
        self._levels: list[list[str]] = self._build_levels(self._leaf_hashes)

    @staticmethod
    def _build_levels(leaf_hashes: list[str]) -> list[list[str]]:
        levels: list[list[str]] = []
        if not leaf_hashes:
            return levels
        current = list(leaf_hashes)
        levels.append(current)
        while len(current) > 1:
            next_level: list[str] = []
            for i in range(0, len(current), 2):
                left = current[i]
                right = current[i + 1] if i + 1 < len(current) else left
                next_level.append(_hash_pair(left, right))
            current = next_level
            levels.append(current)
        return levels

    @property
    def root_hash(self) -> str | None:
        if not self._levels:
            return None
        return self._levels[-1][0]

    @property
    def leaf_count(self) -> int:
        return len(self._leaves)

    def inclusion_proof(self, leaf_index: int) -> InclusionProof:
        """Generate an inclusion proof for the leaf at ``leaf_index``."""
        if not (0 <= leaf_index < self.leaf_count):
            raise IndexError(f"leaf_index {leaf_index} out of range")
        siblings: list[str] = []
        index = leaf_index
        for level in self._levels[:-1]:
            sibling_index = index + 1 if index % 2 == 0 else index - 1
            if sibling_index < len(level):
                siblings.append(level[sibling_index])
            index //= 2
        return InclusionProof(
            leaf_index=leaf_index,
            leaf_hash=self._leaf_hashes[leaf_index],
            sibling_hashes=tuple(siblings),
            root_hash=self.root_hash or "",
        )

    @staticmethod
    def verify_inclusion_proof(proof: InclusionProof, leaf_data: bytes) -> bool:
        """Verify an inclusion proof against the claimed root."""
        current = _leaf_hash(leaf_data)
        if current != proof.leaf_hash:
            return False
        for sibling in proof.sibling_hashes:
            current = _hash_pair(current, sibling)
        return current == proof.root_hash
