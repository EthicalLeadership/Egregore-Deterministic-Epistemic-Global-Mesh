from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from egregore.governance.anchorum_ingest_runner import (
    IngestRecordView,
    IngestRunReport,
)
from egregore.shared.canonical import canonical_json, canonical_loads


@dataclass(frozen=True)
class TailDiffSummary:
    tail_len: int
    differing_indices: list[int]
    payload_key_sets: list[list[str]]
    # deterministic canonical payload representations, best-effort
    left_payload_canon: list[str | None]
    right_payload_canon: list[str | None]


@dataclass(frozen=True)
class AnchorumIngestComparison:
    left: IngestRunReport
    right: IngestRunReport
    verdict: str

    # Basic fields
    left_batch_ingested: int | None
    right_batch_ingested: int | None
    left_verify_chain_ok: bool | None
    right_verify_chain_ok: bool | None

    # Tail payload diffs based on `.zarc` payload contents (best-effort; uses raw_bytes_utf8 stored by the offline collector)
    tail_diffs: TailDiffSummary | None

    # Human-readable metrics summary
    deltas: dict[str, Any]


def _payload_keyset_from_obj(payload: Any) -> list[str]:
    if not isinstance(payload, Mapping):
        return []
    return sorted([str(k) for k in payload])


def _payload_canon(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    # canonical_json ensures deterministic serialization
    return canonical_json(dict(payload))


def _extract_payload_from_record(raw_bytes_utf8: str) -> Any:
    """
    raw_bytes_utf8 is a canonical JSON UTF-8 string of the full `.zarc` entry
    as stored by the offline ingest runner.
    """
    try:
        obj = canonical_loads(raw_bytes_utf8)
    except Exception:
        return {}
    if not isinstance(obj, dict):
        return {}
    payload_val = obj.get("payload")
    return payload_val if isinstance(payload_val, Mapping) else {}


def _compare_tail_payloads(
    *,
    left_records: Sequence[IngestRecordView],
    right_records: Sequence[IngestRecordView],
    max_tail: int,
) -> TailDiffSummary | None:
    if not left_records or not right_records:
        return None

    left_tail = list(left_records[:max_tail])
    right_tail = list(right_records[:max_tail])

    tail_len = min(len(left_tail), len(right_tail))
    if tail_len == 0:
        return None

    differing_indices: list[int] = []
    payload_key_sets: list[list[str]] = []
    left_payload_canon: list[str | None] = []
    right_payload_canon: list[str | None] = []

    for i in range(tail_len):
        l_payload = _extract_payload_from_record(left_tail[i].raw_bytes_utf8)
        r_payload = _extract_payload_from_record(right_tail[i].raw_bytes_utf8)

        l_keys = _payload_keyset_from_obj(l_payload)
        r_keys = _payload_keyset_from_obj(r_payload)
        payload_key_sets.append(sorted(set(l_keys) | set(r_keys)))

        l_canon = _payload_canon(l_payload)
        r_canon = _payload_canon(r_payload)
        left_payload_canon.append(l_canon)
        right_payload_canon.append(r_canon)

        if l_canon != r_canon:
            differing_indices.append(i)

    return TailDiffSummary(
        tail_len=tail_len,
        differing_indices=differing_indices,
        payload_key_sets=payload_key_sets,
        left_payload_canon=left_payload_canon,
        right_payload_canon=right_payload_canon,
    )


def compare_anchorum_ingests(
    *,
    left: IngestRunReport,
    right: IngestRunReport,
    max_tail: int = 50,
) -> AnchorumIngestComparison:
    """
    Compare two AnchorumBridge ingestion runs represented as IngestRunReport.

    This comparison is best-effort and depends on the ingest runner being the repo's
    offline deterministic collector.

    Outputs:
    - batch_ingested delta (if available)
    - verify_chain delta (if signing key was provided to each ingest run)
    - tail payload diffs based on `.zarc` payload content
    """
    left_batch_ingested = left.batch_ingested
    right_batch_ingested = right.batch_ingested

    left_verify_chain_ok = left.verify_chain_ok
    right_verify_chain_ok = right.verify_chain_ok

    tail_diffs = _compare_tail_payloads(
        left_records=left.records,
        right_records=right.records,
        max_tail=max_tail,
    )

    deltas: dict[str, Any] = {
        "batch_ingested_delta": None,
        "verify_chain_delta": None,
        "tail_differing_indices_count": (
            None if tail_diffs is None else len(tail_diffs.differing_indices)
        ),
    }

    if left_batch_ingested is not None and right_batch_ingested is not None:
        deltas["batch_ingested_delta"] = right_batch_ingested - left_batch_ingested

    if left_verify_chain_ok is not None and right_verify_chain_ok is not None:
        deltas["verify_chain_delta"] = (right_verify_chain_ok is True) - (
            left_verify_chain_ok is True
        )

    verdict = "unknown"
    if left_verify_chain_ok is False or right_verify_chain_ok is False:
        verdict = "FAIL_CHAIN_VERIFICATION"
    elif left_batch_ingested is None or right_batch_ingested is None:
        verdict = "INCOMPLETE"
    else:
        verdict = "MATCH" if deltas["tail_differing_indices_count"] == 0 else "DIFF"

    return AnchorumIngestComparison(
        left=left,
        right=right,
        verdict=verdict,
        left_batch_ingested=left_batch_ingested,
        right_batch_ingested=right_batch_ingested,
        left_verify_chain_ok=left_verify_chain_ok,
        right_verify_chain_ok=right_verify_chain_ok,
        tail_diffs=tail_diffs,
        deltas=deltas,
    )
