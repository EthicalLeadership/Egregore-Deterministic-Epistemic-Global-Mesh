from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from egregore.shared.canonical import canonical_json

# Audit-listed priority stack (Phase 2 checks). Kept for deterministic sequencing.
_GATE_PRIORITY_ORDER: tuple[str, ...] = (
    "security",
    "admin",
    "maintenance",
    "logistics",
    "growth",
    "cpu",
    "memory",
    "storage",
    "network",
    "gpu",
    "bus",
    "interconnect",
)


def _stable_hash_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _u32_from_hex_prefix(hex_str: str) -> int:
    return int(hex_str[:8], 16)


def _derive_trace_context(*, wu_seed: str) -> dict[str, Any]:
    h = _stable_hash_hex(wu_seed)
    return {
        "trace_id_hi": _u32_from_hex_prefix(h),
        "trace_id_lo": _u32_from_hex_prefix(h[8:]),
        "span_id": 0,
        "sampled": True,
    }


def _derive_deadline_nanos(*, wu_seed: str) -> int:
    h = _stable_hash_hex("deadline|" + wu_seed)
    return int(h[:15], 16)


@dataclass(frozen=True)
class GateActionProposal:
    gate: str
    action_id: str
    intent: Mapping[str, Any]
    confidence: float

    @staticmethod
    def from_action_envelope(*, envelope: Mapping[str, Any]) -> GateActionProposal:
        # Policy: never use json.loads/json.dumps here (only use canonical_json).
        gate = str(envelope["gate"])

        action_id_raw = envelope.get("action_id")
        if action_id_raw is not None:
            action_id = str(action_id_raw)
        else:
            # action_id derived from canonical JSON of the envelope.
            action_id = _stable_hash_hex(canonical_json(envelope))

        intent_val = envelope.get("intent")
        intent: Mapping[str, Any] = (
            intent_val if isinstance(intent_val, Mapping) else {}
        )

        confidence = float(envelope.get("confidence", 1.0))
        return GateActionProposal(
            gate=gate, action_id=action_id, intent=intent, confidence=confidence
        )


def _gate_priority(gate: str) -> int:
    gate_l = gate.lower().strip()
    if gate_l == "interconnect":
        gate_l = "bus"
    try:
        return _GATE_PRIORITY_ORDER.index(gate_l)
    except ValueError:
        return len(_GATE_PRIORITY_ORDER)


def _sequencing_seed(*, proposals: Sequence[GateActionProposal], event_seq: int) -> str:
    payload = {
        "event_seq": int(event_seq),
        "proposals": [
            {
                "gate": p.gate,
                "action_id": p.action_id,
                "intent": dict(p.intent),
                "confidence": float(p.confidence),
            }
            for p in proposals
        ],
    }
    return canonical_json(payload)


def sequence_gate_proposals_to_workunit_payload(
    *,
    proposals: Sequence[Mapping[str, Any]],
    event_seq: int,
) -> dict[str, Any]:
    """
    Deterministic Phase-2 sequencing.

    Architecture note:
    - This module intentionally avoids importing egregore.dt1 to satisfy the strict
      cross-layer dependency matrix (application layer must not depend on dt1).

    Output:
    - Returns a canonical WorkUnit-like payload as a dict. Downstream bridges can
      map this payload into dt1 WorkUnit objects if/when the full dt1 transit layer
      is integrated.
    """
    typed = [GateActionProposal.from_action_envelope(envelope=env) for env in proposals]
    wu_seed = _sequencing_seed(proposals=typed, event_seq=int(event_seq))

    ordered = sorted(typed, key=lambda p: (_gate_priority(p.gate), p.action_id))

    header_payload = {
        "event_seq": int(event_seq),
        "proposals_ordered": [
            {
                "gate": p.gate,
                "action_id": p.action_id,
                "intent": dict(p.intent),
                "confidence": float(p.confidence),
            }
            for p in ordered
        ],
    }
    header_bytes = canonical_json(header_payload).encode("utf-8")

    wu_hi = _u32_from_hex_prefix(_stable_hash_hex("wu_hi|" + wu_seed))
    wu_lo = _u32_from_hex_prefix(_stable_hash_hex("wu_lo|" + wu_seed))

    return {
        "work_unit": {
            "wu_id_hi": wu_hi,
            "wu_id_lo": wu_lo,
            "tenant_id": 0,
            "dt1_class": "DT1_CLASS_L",
            "dt1_type": "gate_action_sequenced",
            "priority": "P1",
            "deadline_unix_nanos": _derive_deadline_nanos(wu_seed=wu_seed),
            "est_cost_bucket": 0,
            "routing": "ROUTING_CORE_OK",
            "trace": _derive_trace_context(wu_seed=wu_seed),
            "flags": 0,
            "attempt": 0,
        },
        "spans": [
            {
                "kind": "HEADER",
                "index": 0,
                "length_bytes": len(header_bytes),
                "inline_bytes": header_bytes.decode("utf-8"),
            }
        ],
        # For replay-stability / audit correlation
        "sequencing_seed": wu_seed,
    }
