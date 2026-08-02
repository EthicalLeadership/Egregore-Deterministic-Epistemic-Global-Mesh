"""Factory telemetry — append-only JSONL recorder + request context.

Phase 1 measurement for the AI-factory line. Ported from the DFIH
ExecutionRecorder pattern, following the in-repo house style
(``kernel/provenance.py``, ``infrastructure/block_store.py``): canonical JSON,
one line per event, open/write/close per append, monotonic sequence numbers.

Primary sink is JSONL on disk (no NATS dependency). Disable with
``EGREGORE_FACTORY_TELEMETRY=off``; override location with
``EGREGORE_FACTORY_TELEMETRY_DIR``.
"""

from __future__ import annotations

import os
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from egregore.shared.canonical import canonical_dumps, canonical_loads

_DEFAULT_DIR = Path(__file__).resolve().parents[3] / "report" / "factory_telemetry"

# Per-request correlation context, set at endpoint entry and read by the
# station/inference hooks. Never required — hooks no-op when it is unset.
telemetry_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "factory_telemetry_context", default=None
)


def new_run_context(**fields: Any) -> dict[str, Any]:
    """Build a fresh correlation context for one factory run."""
    return {"run_id": uuid.uuid4().hex, **fields}


class NullRecorder:
    """No-op recorder used when telemetry is disabled."""

    def record_event(self, event: dict[str, Any]) -> dict[str, Any]:
        return event

    def export_trace(self) -> list[dict[str, Any]]:
        return []


class FactoryRecorder:
    """Append-only canonical-JSONL event recorder.

    One file per UTC day (``factory_YYYY-MM-DD.jsonl``) inside the configured
    directory so retention/rotation stays trivial.
    """

    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._seq = 0

    def _path(self) -> Path:
        day = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        return self._dir / f"factory_{day}.jsonl"

    def record_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Stamp sequence/timestamp and append one canonical JSON line."""
        if "event_type" not in event:
            raise ValueError("factory telemetry event requires 'event_type'")
        self._seq += 1
        stamped = {
            "seq_no": self._seq,
            "ts": datetime.now(tz=UTC).isoformat(),
            "ts_ns": time.time_ns(),
            **event,
        }
        line = canonical_dumps(stamped, default=str)
        path = self._path()
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return stamped

    def export_trace(self) -> list[dict[str, Any]]:
        """Read back every event recorded today (primarily for tests)."""
        path = self._path()
        if not path.exists():
            return []
        return [canonical_loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


_recorder: FactoryRecorder | NullRecorder | None = None


def get_recorder() -> FactoryRecorder | NullRecorder:
    """Process-wide recorder, configured by environment (lazy singleton)."""
    global _recorder
    if _recorder is None:
        if os.environ.get("EGREGORE_FACTORY_TELEMETRY", "").lower() == "off":
            _recorder = NullRecorder()
        else:
            directory = Path(
                os.environ.get("EGREGORE_FACTORY_TELEMETRY_DIR", str(_DEFAULT_DIR))
            )
            _recorder = FactoryRecorder(directory)
    return _recorder


def reset_recorder() -> None:
    """Drop the singleton (tests only)."""
    global _recorder
    _recorder = None


def emit(event_type: str, **fields: Any) -> None:
    """Record one event, merging the current request context if present.

    Fail-open: telemetry must never break a factory run, so recorder errors
    are swallowed after logging.
    """
    import logging

    try:
        ctx = dict(telemetry_context.get() or {})
        station = ctx.pop("current_station", None)
        payload = ctx
        if station is not None and "station" not in fields:
            fields["station"] = station
        get_recorder().record_event({"event_type": event_type, **payload, **fields})
    except Exception:  # noqa: BLE001
        logging.getLogger("egregore.factory.telemetry").exception(
            "failed to record %s", event_type
        )
