from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from egregore.shared.canonical import canonical_json, canonical_loads


@dataclass(frozen=True)
class VaultIngestRecord:
    case_id: str
    content_type: str
    raw_bytes: bytes
    metadata: Mapping[str, Any]


class AnchorumBridge:
    """
    Bridges `.zarc` JSONL provenance entries into an injected ANCHORUM vault ingest callable.

    Contract:
    - Input: .zarc JSONL lines
    - Output: batches of VaultIngestRecord to the injected vault ingest callable.
    - No direct ANCHORUM imports; injection boundary keeps tests CPU-only + dependency-free.
    """

    def __init__(
        self,
        *,
        zarc_path: str | Path,
        vault_ingest: Callable[[list[Mapping[str, Any]]], Any],
        content_type: str = "application/x-egregore-zarc",
    ) -> None:
        self._zarc_path = Path(zarc_path)
        self._vault_ingest = vault_ingest
        self._content_type = content_type

    def _load_last_n_entries(self, last_n: int) -> list[MutableMapping[str, Any]]:
        if last_n <= 0:
            return []
        if not self._zarc_path.exists():
            return []

        lines = self._zarc_path.read_text(encoding="utf-8").splitlines()
        tail = lines[-last_n:] if lines else []
        out: list[MutableMapping[str, Any]] = []
        for line in tail:
            line = line.strip()
            if not line:
                continue
            obj = canonical_loads(line)
            if not isinstance(obj, dict):
                continue
            out.append(obj)
        return out

    def sync(self, *, last_n: int = 100) -> Any:
        entries = self._load_last_n_entries(last_n)
        if not entries:
            return None

        batch: list[Mapping[str, Any]] = []
        for entry in entries:
            payload = (
                entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
            )
            case_id = (
                payload.get("case_id") or payload.get("caseId") or "egregore-default"
            )
            sig_hex = entry.get("sig")

            rec = {
                "case_id": str(case_id),
                "content_type": self._content_type,
                "raw_bytes": canonical_json(entry).encode("utf-8"),
                "metadata": {
                    "ts_ns": entry.get("ts_ns"),
                    "engine": entry.get("engine"),
                    "event": entry.get("event"),
                    "sig_hex": sig_hex,
                },
            }
            batch.append(rec)

        # Injected callable owns storage integration; tests can verify it was invoked with correct batch length.
        return self._vault_ingest(batch)
