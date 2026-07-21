from __future__ import annotations

from pathlib import Path

from egregore.governance.anchorum_ingest_comparator import compare_anchorum_ingests
from egregore.governance.anchorum_ingest_runner import run_anchorum_bridge_ingest
from egregore.kernel.provenance import Provenance


def _make_zarc(path: Path, *, signing_key_hex: str, payloads: list[dict]) -> None:
    t = {"v": 0}

    def now_ns() -> int:
        t["v"] += 1
        return 10_000 + t["v"]

    prov = Provenance(
        path,
        signing_key_hex=signing_key_hex,
        now_ns=now_ns,
        prev_hash_init="0" * 64,
    )

    for p in payloads:
        prov.append(
            engine="thermal",
            event="PRESSURE_ENERGY",
            payload=p,
            ts_ns=None,
        )


def test_run_anchorum_bridge_ingest_tail_and_verify_chain(tmp_path: Path) -> None:
    signing_key_hex = "01" * 32

    left_zarc = tmp_path / "left.zarc"
    _make_zarc(
        left_zarc,
        signing_key_hex=signing_key_hex,
        payloads=[
            {"temp_c": 50.0, "vram_pct": 10.0, "depth": 0, "gear": 5},
            {"temp_c": 83.0, "vram_pct": 10.0, "depth": 0, "gear": 5},
            {"temp_c": 84.0, "vram_pct": 10.0, "depth": 0, "gear": 5},
        ],
    )

    report = run_anchorum_bridge_ingest(
        zarc_path=left_zarc,
        last_n=2,
        signing_key_hex=signing_key_hex,  # accepted but ignored in governance layer
        record_limit=100,
    )

    assert report.zarc_path == str(left_zarc)
    assert report.last_n_requested == 2
    assert report.batch_ingested == 2
    assert report.verify_chain_ok is None
    assert len(report.records) == 2
    assert all(r.content_type == "application/x-egregore-zarc" for r in report.records)


def test_compare_anchorum_ingests_detects_payload_diff(tmp_path: Path) -> None:
    signing_key_hex = "02" * 32

    left_zarc = tmp_path / "left.zarc"
    right_zarc = tmp_path / "right.zarc"

    _make_zarc(
        left_zarc,
        signing_key_hex=signing_key_hex,
        payloads=[
            {"temp_c": 50.0, "vram_pct": 10.0, "depth": 0, "gear": 5},
            {"temp_c": 83.0, "vram_pct": 10.0, "depth": 0, "gear": 5},
            {"temp_c": 84.0, "vram_pct": 10.0, "depth": 0, "gear": 5},
        ],
    )

    # Make the last payload different (tail index 1 for last_n=2).
    _make_zarc(
        right_zarc,
        signing_key_hex=signing_key_hex,
        payloads=[
            {"temp_c": 50.0, "vram_pct": 10.0, "depth": 0, "gear": 5},
            {"temp_c": 83.0, "vram_pct": 10.0, "depth": 0, "gear": 5},
            {"temp_c": 91.0, "vram_pct": 10.0, "depth": 0, "gear": 5},
        ],
    )

    left_report = run_anchorum_bridge_ingest(
        zarc_path=left_zarc,
        last_n=2,
        signing_key_hex=signing_key_hex,  # accepted but ignored in governance layer
        record_limit=100,
    )
    right_report = run_anchorum_bridge_ingest(
        zarc_path=right_zarc,
        last_n=2,
        signing_key_hex=signing_key_hex,  # accepted but ignored in governance layer
        record_limit=100,
    )

    comparison = compare_anchorum_ingests(
        left=left_report, right=right_report, max_tail=50
    )

    assert left_report.verify_chain_ok is None
    assert right_report.verify_chain_ok is None
    assert comparison.left_batch_ingested == 2
    assert comparison.right_batch_ingested == 2
    assert comparison.verdict == "DIFF"

    assert comparison.tail_diffs is not None
    assert comparison.tail_diffs.tail_len == 2
    # tail order corresponds to last 2 entries: payloads[1] then payloads[2]
    # We changed payloads[2] only, so differing index should be [1]
    assert comparison.tail_diffs.differing_indices == [1]
