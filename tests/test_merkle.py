"""Tests for the SEL-X Merkle tree implementation."""

from __future__ import annotations

from egregore.shared.merkle import MerkleTree


def test_empty_tree_has_no_root() -> None:
    tree = MerkleTree([])
    assert tree.root_hash is None
    assert tree.leaf_count == 0


def test_single_leaf_root_equals_leaf_hash() -> None:
    tree = MerkleTree([b"hello"])
    assert tree.root_hash is not None
    assert tree.leaf_count == 1


def test_two_leaf_root_is_pair_hash() -> None:
    tree1 = MerkleTree([b"a", b"b"])
    tree2 = MerkleTree([b"a", b"b"])
    assert tree1.root_hash == tree2.root_hash
    assert tree1.root_hash is not None


def test_different_leads_different_root() -> None:
    tree1 = MerkleTree([b"a", b"b"])
    tree2 = MerkleTree([b"a", b"c"])
    assert tree1.root_hash != tree2.root_hash


def test_inclusion_proof_verifies() -> None:
    leaves = [b"alpha", b"beta", b"gamma", b"delta"]
    tree = MerkleTree(leaves)
    for i, leaf in enumerate(leaves):
        proof = tree.inclusion_proof(i)
        assert MerkleTree.verify_inclusion_proof(proof, leaf) is True


def test_inclusion_proof_fails_for_tampered_leaf() -> None:
    leaves = [b"alpha", b"beta", b"gamma"]
    tree = MerkleTree(leaves)
    proof = tree.inclusion_proof(1)
    assert MerkleTree.verify_inclusion_proof(proof, b"tampered") is False
