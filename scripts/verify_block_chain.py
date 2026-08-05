#!/usr/bin/env python3
"""Verify a SEL-X execution block chain (``blocks.zarc`` JSONL store).

Fail-closed: exits non-zero on the first violated block. A chain that
cannot be fully recomputed (legacy records serialized as repr strings)
is reported as LEGACY and still has its linkage and signatures checked.

Usage:
    python scripts/verify_block_chain.py [path] [--pubkey HEX]

Defaults:
    path    : $EGREGORE_DATA_DIR/blocks.zarc
              (or ~/egregore_data/$EGREGORE_NODE_ID/blocks.zarc)
    pubkey  : --pubkey > $EGREGORE_VERIFY_KEY_HEX > derived from
              $EGREGORE_SIGNING_KEY_HEX
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

from egregore.kernel.ed25519_signer import get_verify_key_hex, verify_message
from egregore.shared.canonical import canonical_dumps, canonical_loads
from egregore.shared.merkle import MerkleTree

GENESIS_HASH = "0" * 64


def _recompute_merkle_root(records: list[dict]) -> str | None:
    leaves = [
        canonical_dumps(record, default=str).encode("utf-8") for record in records
    ]
    return MerkleTree(leaves).root_hash


def _recompute_integrity_hash(block: dict, record_hashes: list[str]) -> str:
    payload = (
        f"{block['block_id']}|{block['block_seq']}|{block['previous_block_hash']}|"
        f"{block['merkle_root']}|{block['record_count']}|{','.join(record_hashes)}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_chain(path: Path, verify_key_hex: str | None) -> int:
    """Verify the block chain at ``path``. Returns process exit code."""
    if not path.exists():
        print(f"FAIL: chain file not found: {path}")
        return 2

    previous_integrity = GENESIS_HASH
    blocks_seen = 0

    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            raw = raw.strip()
            if not raw:
                continue
            blocks_seen += 1
            problems: list[str] = []
            notes: list[str] = []

            try:
                block = canonical_loads(raw)
            except Exception as exc:  # noqa: BLE001 - fail-closed on any parse error
                print(f"block line {line_no}: FAIL (unparseable: {exc})")
                return 1

            # 1. Linkage
            if block.get("previous_block_hash") != previous_integrity:
                problems.append(
                    "CHAIN_BREAK: previous_block_hash does not match prior "
                    "integrity hash"
                )

            # 2. Record count consistency
            records = block.get("records") or []
            if block.get("record_count") not in (len(records), 0):
                problems.append(
                    f"RECORD_COUNT_MISMATCH: stored={block.get('record_count')} "
                    f"actual={len(records)}"
                )

            # 3+4. Merkle root and integrity hash recomputation
            integrity = block.get("integrity_hash") or ""
            if records and all(isinstance(r, dict) for r in records):
                merkle = _recompute_merkle_root(records)
                if merkle != block.get("merkle_root"):
                    problems.append("MERKLE_MISMATCH: recomputed root differs")
                record_hashes = [str(r.get("integrity_hash")) for r in records]
                recomputed = _recompute_integrity_hash(block, record_hashes)
                if recomputed != integrity:
                    problems.append("INTEGRITY_MISMATCH: recomputed hash differs")
            else:
                notes.append(
                    "LEGACY: records not independently recomputable "
                    "(repr-serialized); linkage/signature checks only"
                )

            # 5. Signature
            signature = block.get("block_signature") or ""
            if signature:
                if not verify_key_hex:
                    problems.append(
                        "SIGNATURE_UNVERIFIABLE: block is signed but no verify "
                        "key was provided (--pubkey / EGREGORE_VERIFY_KEY_HEX)"
                    )
                elif not verify_message(
                    verify_key_hex=verify_key_hex,
                    message=integrity.encode("utf-8"),
                    signature_hex=signature,
                ):
                    problems.append("SIGNATURE_INVALID")

            status = "OK" if not problems else "FAIL"
            print(
                f"block seq={block.get('block_seq')} height={block.get('block_height')} "
                f"records={len(records)}: {status}"
            )
            for note in notes:
                print(f"  note: {note}")
            if problems:
                for problem in problems:
                    print(f"  violation: {problem}")
                return 1

            previous_integrity = integrity or previous_integrity

    if blocks_seen == 0:
        print(f"FAIL: no blocks found in {path}")
        return 2
    print(f"chain verified: {blocks_seen} block(s), no violations")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default=None, help="blocks.zarc path")
    parser.add_argument("--pubkey", default=None, help="Ed25519 verify key hex")
    args = parser.parse_args()

    if args.path:
        path = Path(args.path)
    else:
        data_dir = os.environ.get("EGREGORE_DATA_DIR")
        if not data_dir:
            node_id = os.environ.get("EGREGORE_NODE_ID", "pioneer1")
            data_dir = f"~/egregore_data/{node_id}"
        path = Path(data_dir).expanduser() / "blocks.zarc"

    verify_key_hex = (
        args.pubkey
        or os.environ.get("EGREGORE_VERIFY_KEY_HEX")
        or (
            get_verify_key_hex(os.environ["EGREGORE_SIGNING_KEY_HEX"])
            if os.environ.get("EGREGORE_SIGNING_KEY_HEX")
            else None
        )
    )
    return verify_chain(path, verify_key_hex)


if __name__ == "__main__":
    sys.exit(main())
