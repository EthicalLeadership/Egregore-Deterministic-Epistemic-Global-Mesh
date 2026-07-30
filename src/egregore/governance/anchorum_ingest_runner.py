# epistemic marker: provenance / auditability
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from egregore.governance.anchorum_bridge import AnchorumBridge
from egregore.shared.canonical import canonical_loads


@dataclass(frozen=True)
class IngestRecordView:
    case_id: str
    content_type: str
    raw_bytes_utf8: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class IngestRunReport:
    zarc_path: str
    last_n_requested: int
    batch_ingested: int | None
    records: list[IngestRecordView]
    # IMPORTANT: governance layer cannot import kernel to verify chain.
    verify_chain_ok: bool | None


class OfflineVaultIngestCollector:
    """
    Offline/deterministic ANCHORUM ingest substitute.

    AnchorumBridge calls an injected callable: `vault_ingest(batch)`.
    This collector records the batch and returns a deterministic dict.
    """

    def __init__(self, *, record_limit: int = 10000) -> None:
        self._record_limit = int(record_limit)
        self.batches: list[list[Mapping[str, Any]]] = []

    def vault_ingest(self, batch: list[Mapping[str, Any]]) -> dict[str, Any]:
        self.batches.append(list(batch))
        return {"ingested": len(batch)}


def _maybe_decode_raw_bytes_utf8(raw_bytes: Any) -> str:
    if isinstance(raw_bytes, (bytes, bytearray)):
        return bytes(raw_bytes).decode("utf-8", errors="strict")
    if isinstance(raw_bytes, str):
        return raw_bytes
    # Governance layer must avoid json.dumps/json.loads (repo rule).
    # Best-effort deterministic stringification:
    return str(raw_bytes)


def _build_records_from_batch(
    batch: Sequence[Mapping[str, Any]],
) -> list[IngestRecordView]:
    records: list[IngestRecordView] = []
    for rec in batch:
        case_id = str(rec.get("case_id", ""))
        content_type = str(rec.get("content_type", ""))
        raw_bytes_utf8 = _maybe_decode_raw_bytes_utf8(rec.get("raw_bytes"))

        metadata_val = rec.get("metadata")
        if metadata_val is None:
            metadata: Mapping[str, Any] = {}
        elif isinstance(metadata_val, Mapping):
            metadata = metadata_val
        else:
            metadata = {"metadata_raw": str(metadata_val)}

        records.append(
            IngestRecordView(
                case_id=case_id,
                content_type=content_type,
                raw_bytes_utf8=raw_bytes_utf8,
                metadata=metadata,
            )
        )
    return records


def run_anchorum_bridge_ingest(
    *,
    zarc_path: Path,
    last_n: int,
    signing_key_hex: str | None = None,
    content_type: str = "application/x-egregore-zarc",
    record_limit: int = 10000,
) -> IngestRunReport:
    """
    Run AnchorumBridge end-to-end against an on-disk `.zarc` file, using an
    offline deterministic `vault_ingest` collector.

    Notes:
    - This module intentionally does NOT verify `.zarc` signatures/hash-chains.
      Repo architecture tests forbid governance-layer importing `egregore.kernel`.
    - `signing_key_hex` is accepted for API ergonomics but ignored in this layer.

    """
    if last_n <= 0:
        raise ValueError("--last-n must be > 0")

    collector = OfflineVaultIngestCollector(record_limit=record_limit)

    bridge = AnchorumBridge(
        zarc_path=zarc_path,
        vault_ingest=collector.vault_ingest,
        content_type=content_type,
    )

    bridge_result = bridge.sync(last_n=last_n)

    batch_ingested: int | None = None
    if (
        bridge_result is not None
        and isinstance(bridge_result, Mapping)
        and "ingested" in bridge_result
    ):
        try:
            batch_ingested = int(bridge_result["ingested"])
        except Exception:
            batch_ingested = None

    all_records: list[IngestRecordView] = []
    if collector.batches:
        # Current AnchorumBridge implementation calls vault_ingest once per sync().
        first_batch = collector.batches[0]
        all_records = _build_records_from_batch(first_batch)
        if len(all_records) > record_limit:
            all_records = all_records[:record_limit]

    # Cannot verify chain in governance layer.
    verify_chain_ok: bool | None = None

    return IngestRunReport(
        zarc_path=str(zarc_path),
        last_n_requested=last_n,
        batch_ingested=batch_ingested,
        records=all_records,
        verify_chain_ok=verify_chain_ok,
    )


def _extract_tail_entries_from_ingest_records(
    records: Sequence[IngestRecordView],
) -> list[dict[str, Any]]:
    """
    Parse the canonical `.zarc` entry JSON from the collector's `raw_bytes_utf8`.
    """
    parsed: list[dict[str, Any]] = []
    for r in records:
        obj = canonical_loads(r.raw_bytes_utf8)
        if isinstance(obj, dict):
            parsed.append(obj)
    return parsed


def get_tail_payload_signature(
    *,
    records: Sequence[IngestRecordView],
    payload_key_order: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Convert ingest records into a normalized representation for diffing:
    - ts_ns, engine, event
    - payload (normalized keys)
    """
    tail_entries = _extract_tail_entries_from_ingest_records(records)
    out: list[dict[str, Any]] = []

    for e in tail_entries:
        engine = e.get("engine")
        event = e.get("event")
        payload_val = e.get("payload")
        if not isinstance(payload_val, Mapping):
            payload_val = {}

        payload_norm: dict[str, Any] = {}
        if payload_key_order:
            for k in payload_key_order:
                if k in payload_val:
                    payload_norm[k] = payload_val[k]
        for k, v in payload_val.items():
            payload_norm[str(k)] = v

        out.append(
            {
                "ts_ns": e.get("ts_ns"),
                "engine": engine,
                "event": event,
                "payload": payload_norm,
            }
        )
    return out


# Integrity gate hook for updater/CI
def run_anchorum_check():
    from egregore.governance.anchorum_integrity_gate import (
        run_anchorum_check as _check,
    )

    return _check()
