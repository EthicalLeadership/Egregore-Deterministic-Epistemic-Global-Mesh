from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from egregore.shared.canonical import canonical_loads


@dataclass(frozen=True)
class FaultInjection:
    reason: str | None
    active: bool


@dataclass(frozen=True)
class ScheduleStep:
    name: str
    fault_injection: FaultInjection | None


@dataclass(frozen=True)
class ExecutionTrace:
    scenario_id: str
    timestamp_ns: int
    stage: ScheduleStep
    metadata: Mapping[str, Any]


def _require_str(obj: Mapping[str, Any], key: str) -> str:
    v = obj[key]
    if not isinstance(v, str):
        raise ValueError(f"dfih_bridge: {key} must be a string")
    return v


def _require_int(obj: Mapping[str, Any], key: str) -> int:
    v = obj[key]
    if not isinstance(v, int):
        raise ValueError(f"dfih_bridge: {key} must be an int")
    return v


def zarc_lines_to_execution_traces(lines: Iterable[str]) -> Iterator[ExecutionTrace]:
    """
    Convert `.zarc` JSONL lines into ExecutionTrace records.

    Contract:
    - Each input line must contain:
      - ts_ns (int)
      - engine (str)          (used only as metadata)
      - event (str)           (mapped to stage.name)
      - payload (object)     (used for metadata; optional fault injection)
      - prev_hash (str)      (metadata)
      - sig (str)            (metadata; bridge does not verify signatures)
    - Strict mapping: missing required fields raise ValueError.
    - No silent field loss: unknown payload fields are preserved in metadata.
    """
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        obj = canonical_loads(line)

        if not isinstance(obj, dict):
            raise ValueError("dfih_bridge: zarc line must be a JSON object")

        ts_ns = _require_int(obj, "ts_ns")
        event = _require_str(obj, "event")
        payload_val = obj.get("payload")
        if payload_val is None:
            payload_val = {}
        if not isinstance(payload_val, dict):
            raise ValueError("dfih_bridge: payload must be an object when present")

        engine = obj.get("engine")
        if not isinstance(engine, str):
            raise ValueError("dfih_bridge: engine must be a string")

        prev_hash = obj.get("prev_hash")
        if not isinstance(prev_hash, str):
            raise ValueError("dfih_bridge: prev_hash must be a string")

        sig = obj.get("sig")
        if not isinstance(sig, str):
            raise ValueError("dfih_bridge: sig must be a string")

        scenario_id = str(
            payload_val.get("wu_id", payload_val.get("scenario_id", prev_hash))
        )

        fault_reason = payload_val.get("reason")
        if fault_reason is not None and not isinstance(fault_reason, str):
            raise ValueError(
                "dfih_bridge: payload.reason must be a string when present"
            )

        # Format-only parser rule:
        # - fault_injection becomes active iff payload.reason is present.
        # - Do not infer fault semantics from engine name in the bridge layer.
        fault_injection = (
            FaultInjection(reason=fault_reason, active=True)
            if fault_reason is not None
            else None
        )

        stage = ScheduleStep(name=event, fault_injection=fault_injection)

        metadata = dict(payload_val)
        metadata.update(
            {
                "engine": engine,
                "prev_hash": prev_hash,
                "sig": sig,
                "zarc_event": event,
            }
        )

        yield ExecutionTrace(
            scenario_id=scenario_id,
            timestamp_ns=ts_ns,
            stage=stage,
            metadata=metadata,
        )


def file_zarc_to_execution_traces(path: str) -> Iterator[ExecutionTrace]:
    with open(path, encoding="utf-8") as f:
        yield from zarc_lines_to_execution_traces(f)
