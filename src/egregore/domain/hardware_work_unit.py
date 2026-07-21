from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Any

from egregore.shared.canonical import canonical_json


class GearMode(StrEnum):
    TURBO = "TURBO"
    ECO = "ECO"
    EMERGENCY_FREEZE = "EMERGENCY_FREEZE"


class PrecisionGear(StrEnum):
    FP16 = "FP16"
    FP32 = "FP32"
    INT8 = "INT8"
    # Note: in this skeleton we treat INT8 as "emulated" unless a backend provides true INT8 GEMM.


@dataclass(frozen=True)
class DTProfile:
    """
    Deterministic thermal / device profile snapshot.

    This is the domain input for "pure" mode selection.
    """

    dt_id: str  # deterministic identifier for the profile (derived externally)
    gear_mode: GearMode

    # Thermal / power readings (best-effort; values should be deterministic per probe run).
    gpu_index: int
    temp_c: float
    vram_pct: float

    # Precision/throughput targets for the work matrix kernel.
    precision: PrecisionGear
    batch_window: int
    concurrency: int

    # Freeze threshold semantics (domain-owned).
    emergency_temp_c: float = 88.0
    freeze_temp_c: float = 88.0


@dataclass(frozen=True)
class TurbineUnit:
    """
    A deterministic "worker identity" representing one TU slot.

    In this skeleton the TU is a logical worker; the backend may map it to CPU/GPU resources.
    """

    tu_id: str
    worker_index: int


@dataclass(frozen=True)
class WorkPayload:
    """
    A batch unit of matrix work.

    Matrices are generated deterministically from seed in the runner; we keep the payload small.
    """

    payload_id: str
    matrix_size: int
    seed: int
    precision: PrecisionGear
    batch_window: int
    tu_id: str


def _as_canonical_mapping(obj: Any) -> Mapping[str, Any]:
    """
    Deterministic canonical mapping for domain objects.
    """
    if hasattr(obj, "__dict__"):
        # dataclasses are handled by canonical_json on dict conversion; keep explicit for robustness.
        return {k: getattr(obj, k) for k in dir(obj) if not k.startswith("_")}
    raise TypeError(f"Unsupported canonical object type: {type(obj).__name__}")


def domain_fingerprint(obj: Any) -> str:
    """
    Canonical JSON fingerprint of a domain object.
    """
    # Avoid nondeterministic repr; canonical_json is deterministic.
    return (
        canonical_json(
            obj
            if isinstance(obj, (dict, list, str, int, float, bool, type(None)))
            else _as_dict(obj)
        )
        .encode("utf-8")
        .decode("utf-8")
    )


def _as_dict(obj: Any) -> Mapping[str, Any]:
    if hasattr(obj, "__dataclass_fields__"):
        # dataclasses -> stable mapping via attribute names.
        # Enum values are represented as their .value to avoid Python-specific enum repr.
        out: dict[str, Any] = {}
        for k in obj.__dataclass_fields__:  # type: ignore[attr-defined]  # compatibility: dataclass field introspection via __dataclass_fields__
            v = getattr(obj, k)
            if isinstance(v, Enum):
                out[k] = v.value
            else:
                out[k] = v
        return out
    if isinstance(obj, Enum):
        return {"value": obj.value}
    if isinstance(obj, Mapping):
        return obj
    raise TypeError(f"Unsupported domain object for as_dict: {type(obj).__name__}")


def canonical_fingerprint(obj: Any) -> str:
    """
    Canonical JSON bytes -> stable hex-like identifier via sha256 of canonical JSON.

    Keeping it in-domain lets the benchmark script and runner create deterministic IDs.
    """
    import hashlib

    payload = canonical_json(_as_dict(obj)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
