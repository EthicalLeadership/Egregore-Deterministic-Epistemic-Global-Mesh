from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nacl.signing import SigningKey, VerifyKey

from egregore.shared.canonical import canonical_json, canonical_loads, sha256_hex

CanonicalJson = str


@dataclass(frozen=True)
class ZarcEntry:
    ts_ns: int
    engine: str
    event: str
    payload: Mapping[str, Any]
    prev_hash: str
    sig: str  # Ed25519 signature (hex) over canonical JSON bytes of the unsigned entry.


class Provenance:
    """
    Writes a JSONL “.zarc” stream where each line commits:
    - timestamp (ts_ns)
    - engine/event + payload
    - prev_hash: SHA256(chain) of the previous canonical line
    - sig: Ed25519 signature over canonical JSON bytes of the unsigned entry
    """

    def __init__(
        self,
        zarc_path: str | Path,
        *,
        signing_key_hex: str,
        prev_hash_init: str | None = None,
        now_ns: Callable[[], int] | None = None,
    ) -> None:
        self.zarc_path = Path(zarc_path)
        self.zarc_path.parent.mkdir(parents=True, exist_ok=True)

        self._signing_key = SigningKey(bytes.fromhex(signing_key_hex))
        self._verify_key: VerifyKey = self._signing_key.verify_key
        self._now_ns = now_ns or (lambda: int(__import__("time").time_ns()))

        self._prev_hash = prev_hash_init or ("0" * 64)
        # Load last hash if file exists and has content; preserves append semantics.
        if self.zarc_path.exists():
            last = self._read_last_line(self.zarc_path)
            if last is not None:
                # hash chain: prev_hash for next entry is SHA256(canonical_line_bytes_of_last)
                self._prev_hash = sha256_hex((last + "\n").encode("utf-8"))

    @property
    def verify_key_hex(self) -> str:
        return self._verify_key.encode().hex()

    def append(
        self,
        *,
        engine: str,
        event: str,
        payload: Mapping[str, Any] | None = None,
        ts_ns: int | None = None,
    ) -> str:
        payload = payload or {}
        entry_unsigned: MutableMapping[str, Any] = {
            "ts_ns": int(ts_ns if ts_ns is not None else self._now_ns()),
            "engine": engine,
            "event": event,
            "payload": dict(payload),
            "prev_hash": self._prev_hash,
        }

        unsigned_bytes = canonical_json(entry_unsigned).encode("utf-8")
        sig_hex = self._signing_key.sign(unsigned_bytes).signature.hex()

        entry_final: MutableMapping[str, Any] = dict(entry_unsigned)
        entry_final["sig"] = sig_hex

        line = canonical_json(entry_final)

        with self.zarc_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()

        # Update chain hash for the next call.
        current_hash = sha256_hex((line + "\n").encode("utf-8"))
        self._prev_hash = current_hash
        return current_hash

    def verify_line(self, line: str) -> bool:
        obj = canonical_loads(line)
        if not isinstance(obj, dict):
            return False
        if "sig" not in obj:
            return False

        sig_hex = obj["sig"]
        sig_bytes = bytes.fromhex(sig_hex)

        sigless: MutableMapping[str, Any] = dict(obj)
        del sigless["sig"]
        unsigned_bytes = canonical_json(sigless).encode("utf-8")

        try:
            self._verify_key.verify(unsigned_bytes, sig_bytes)
            return True
        except Exception:
            return False

    def iter_entries(self) -> Iterable[ZarcEntry]:
        if not self.zarc_path.exists():
            return []

        def gen() -> Iterable[ZarcEntry]:
            with self.zarc_path.open("r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line:
                        continue
                    obj = canonical_loads(line)
                    yield ZarcEntry(
                        ts_ns=int(obj["ts_ns"]),
                        engine=str(obj["engine"]),
                        event=str(obj["event"]),
                        payload=obj.get("payload") or {},
                        prev_hash=str(obj["prev_hash"]),
                        sig=str(obj["sig"]),
                    )

        return gen()

    def verify_chain(self) -> bool:
        prev_expected = "0" * 64
        for line in self._iter_lines():
            if not self.verify_line(line):
                return False

            obj = canonical_loads(line)
            if str(obj["prev_hash"]) != prev_expected:
                return False

            prev_expected = sha256_hex((line + "\n").encode("utf-8"))
        return True

    def iter_lines(self) -> Iterable[str]:
        """Public read of raw canonical lines (for checkpointing/auditing)."""
        return self._iter_lines()

    def _iter_lines(self) -> Iterable[str]:
        if not self.zarc_path.exists():
            return []
        with self.zarc_path.open("r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if line:
                    yield line

    @staticmethod
    def _read_last_line(path: Path) -> str | None:
        if not path.exists():
            return None
        # Keep it simple (datasets are small for MVP/tests).
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines:
            return None
        return lines[-1]
