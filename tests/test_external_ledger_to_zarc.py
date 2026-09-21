from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from egregore.kernel.external_ledger_to_zarc import (
    convert_external_hash_ledger_to_zarc,
)
from egregore.kernel.provenance import Provenance


def _canonical_json_bytes(data: dict[str, Any]) -> bytes:
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _make_external_hash_chain_ledger(
    path: Path, *, node_id: str, events: list[dict[str, Any]]
) -> None:
    """
    Create an external hash-chained JSONL ledger compatible with:
    - external core/efficiency/ledger.py hashing:
      entry_hash = sha256(json.dumps(entry_without_hash, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    - prev_hash = "GENESIS" then previous entry's "hash"
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    prev_hash = "GENESIS"
    seq = 0

    with path.open("w", encoding="utf-8") as f:
        for ev in events:
            seq += 1
            entry = {
                "seq": seq,
                "ts": ev.get("ts", "2026-01-01T00:00:00+00:00"),
                "node": node_id,
                "event": ev["event"],
                "details": ev.get("details", {}),
                "prev_hash": prev_hash,
            }
            entry_wo_hash = dict(entry)
            entry_hash = _sha256_hex(_canonical_json_bytes(entry_wo_hash))
            entry["hash"] = entry_hash
            f.write(
                json.dumps(
                    entry, sort_keys=False, separators=(",", ":"), ensure_ascii=False
                )
                + "\n"
            )
            prev_hash = entry_hash


def test_convert_external_hash_ledger_to_zarc_and_verify_chain(tmp_path: Path) -> None:
    signing_key_hex = "03" * 32

    external_ledger_path = tmp_path / "external_ledger.jsonl"
    zarc_out_path = tmp_path / "out.zarc"

    _make_external_hash_chain_ledger(
        external_ledger_path,
        node_id="node-A",
        events=[
            {"event": "E1", "details": {"a": 1}, "ts": "2026-01-01T00:00:00+00:00"},
            {"event": "E2", "details": {"b": 2}, "ts": "2026-01-01T00:00:01+00:00"},
        ],
    )

    report = convert_external_hash_ledger_to_zarc(
        external_ledger_jsonl_path=external_ledger_path,
        zarc_path=zarc_out_path,
        signing_key_hex=signing_key_hex,
        provenance_engine="external_ledger_test",
        provenance_event_prefix="ledger_",
        verify_external=True,
        fail_fast=True,
    )

    assert report["external_verified"] is True
    assert report["external_entry_count"] == 2
    assert report["zarc_path"] == str(zarc_out_path)
    assert report["zarc_verify_chain_ok"] is True

    # Ensure `.zarc` lines exist and are parseable.
    assert zarc_out_path.exists()
    prov = Provenance(
        zarc_out_path,
        signing_key_hex=signing_key_hex,
        prev_hash_init="0" * 64,
    )
    assert prov.verify_chain() is True
