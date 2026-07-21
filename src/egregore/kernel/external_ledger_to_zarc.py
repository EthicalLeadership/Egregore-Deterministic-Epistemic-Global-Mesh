from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

from egregore.kernel.provenance import Provenance
from egregore.shared.canonical import canonical_json, canonical_loads

_ISO8601_NS_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})"
    r"(?:\.(?P<frac>\d{1,9}))?"
    r"(?P<tz>Z|[+-]\d{2}:\d{2})$"
)


def _iso8601_to_epoch_ns(iso: str) -> int:
    """
    Convert ISO8601 timestamps to epoch nanoseconds.

    Supports:
    - ...Z
    - ...+HH:MM / ...-HH:MM
    Fractional seconds supported up to 9 digits; if fewer digits, it is right-padded.
    """
    m = _ISO8601_NS_RE.match(iso.strip())
    if not m:
        raise ValueError(
            f"external_ledger_to_zarc: unrecognized timestamp format: {iso!r}"
        )

    date_s = m.group("date")
    hh = int(m.group("h"))
    mm = int(m.group("m"))
    ss = int(m.group("s"))
    frac = m.group("frac") or "0"
    tz = m.group("tz")

    # Normalize fractional to 9 digits (ns).
    frac = (frac + "0" * 9)[:9]
    ns = int(frac)

    if tz == "Z":
        offset = UTC
    else:
        sign = 1 if tz[0] == "+" else -1
        tzh = int(tz[1:3])
        tzm = int(tz[4:6])
        offset = timezone(sign * (tzh * 3600 + tzm * 60))

    dt = datetime(
        int(date_s[0:4]),
        int(date_s[5:7]),
        int(date_s[8:10]),
        hh,
        mm,
        ss,
        tzinfo=offset,
    )

    dt_utc = dt.astimezone(UTC)
    epoch_s = int(dt_utc.timestamp())
    return epoch_s * 1_000_000_000 + ns


def _read_jsonl_lines(path: Path) -> Iterator[str]:
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if line:
                yield line


def _compute_external_hash(entry_without_hash: Mapping[str, Any]) -> str:
    """
    Mirror external ledger.py hashing rule:

    sha256(canonical_dumps(entry_without_hash, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    We use canonical_json() which applies sort_keys + separators(",", ":").
    """
    payload = canonical_json(dict(entry_without_hash)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def iter_external_hash_chained_ledger(
    *, ledger_jsonl_path: Path
) -> Iterator[dict[str, Any]]:
    for line in _read_jsonl_lines(ledger_jsonl_path):
        obj = canonical_loads(line)
        if isinstance(obj, dict):
            yield obj


def verify_external_hash_chain(
    *, ledger_jsonl_path: Path, fail_fast: bool = True
) -> tuple[bool, str | None]:
    """
    Verify the external hash-chained JSONL ledger integrity.

    Contract expected (from external core/efficiency/ledger.py):
    - each line has prev_hash and hash
    - hash = sha256(canonical_json(entry_without_hash_bytes))
    - first prev_hash is "GENESIS"
    - subsequent prev_hash equals prior hash
    """
    prev_expected_hash = "GENESIS"
    for idx, obj in enumerate(
        iter_external_hash_chained_ledger(ledger_jsonl_path=ledger_jsonl_path), start=1
    ):
        entry = dict(obj)
        if "prev_hash" not in entry or "hash" not in entry:
            msg = f"entry {idx}: missing prev_hash/hash"
            if fail_fast:
                return False, msg
            continue

        prev_hash = str(entry.get("prev_hash"))
        expected_prev = prev_expected_hash
        if prev_hash != expected_prev:
            msg = f"entry {idx}: prev_hash mismatch (got={prev_hash!r} expected={expected_prev!r})"
            return False, msg

        entry_hash = str(entry.get("hash"))
        entry_wo_hash = dict(entry)
        entry_wo_hash.pop("hash", None)

        recomputed = _compute_external_hash(entry_wo_hash)
        if entry_hash != recomputed:
            msg = f"entry {idx}: hash mismatch (got={entry_hash!r} expected={recomputed!r})"
            return False, msg

        prev_expected_hash = entry_hash

    return True, None


def convert_external_hash_ledger_to_zarc(
    *,
    external_ledger_jsonl_path: Path,
    zarc_path: Path,
    signing_key_hex: str,
    provenance_engine: str = "external_ledger",
    provenance_event_prefix: str = "ledger_",
    verify_external: bool = True,
    fail_fast: bool = True,
) -> dict[str, Any]:
    """
    Convert an external hash-chained JSONL ledger into this repo's signed `.zarc` chain.
    """
    if verify_external:
        ok, reason = verify_external_hash_chain(
            ledger_jsonl_path=external_ledger_jsonl_path,
            fail_fast=fail_fast,
        )
        if not ok:
            raise ValueError(f"external ledger chain verification failed: {reason}")

    zarc_path.parent.mkdir(parents=True, exist_ok=True)

    z = Provenance(
        zarc_path,
        signing_key_hex=signing_key_hex,
        prev_hash_init=None,
        now_ns=None,
    )

    external_count = 0
    for obj in iter_external_hash_chained_ledger(
        ledger_jsonl_path=external_ledger_jsonl_path
    ):
        external_count += 1
        external_event = str(obj.get("event", "unknown"))
        external_ts = obj.get("ts")

        ts_ns: int | None
        if isinstance(external_ts, str):
            try:
                ts_ns = _iso8601_to_epoch_ns(external_ts)
            except Exception:
                ts_ns = None
        else:
            ts_ns = None

        payload: Mapping[str, Any] = {
            "seq": obj.get("seq"),
            "ts": external_ts,
            "node": obj.get("node"),
            "event": external_event,
            "details": (
                obj.get("details") if isinstance(obj.get("details"), Mapping) else {}
            ),
            "prev_hash": obj.get("prev_hash"),
            "hash": obj.get("hash"),
        }

        z.append(
            engine=provenance_engine,
            event=f"{provenance_event_prefix}{external_event}",
            payload=payload,
            ts_ns=ts_ns,
        )

    return {
        "external_verified": bool(verify_external),
        "external_entry_count": external_count,
        "zarc_path": str(zarc_path),
        "zarc_verify_chain_ok": z.verify_chain(),
    }
