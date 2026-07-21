from __future__ import annotations

from dataclasses import dataclass, field

from egregore.domain.semantics.projection_descriptor import (
    BindingAuditRecord,
    ProjectionDescriptor,
)
from egregore.interface.constraint_binding_ports import IBindingAuditEmitter
from egregore.shared.canonical import canonical_json, canonical_loads, sha256_hex


def _compute_registry_hash(
    descriptors: dict[tuple[str, str], ProjectionDescriptor],
) -> str:
    # Deterministic commitment over descriptor canonical hashes.
    items = sorted(
        (
            (agent_id, version, desc.canonical_hash())
            for (agent_id, version), desc in descriptors.items()
        ),
        key=lambda x: (x[0], x[1]),
    )
    payload = {"descriptors": items}
    return sha256_hex(canonical_json(payload).encode("utf-8"))


def _compute_runtime_state_hash(runtime_state_repr: str) -> str:
    return sha256_hex(runtime_state_repr.encode("utf-8"))


def _extract_runtime_descriptor_items(
    runtime_state_repr: str,
) -> list[tuple[str, str, str]] | None:
    try:
        payload = canonical_loads(runtime_state_repr)
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    descriptors_raw = payload.get("descriptors")
    if not isinstance(descriptors_raw, list):
        return None

    extracted: list[tuple[str, str, str]] = []
    for item in descriptors_raw:
        if not isinstance(item, (list, tuple)):
            return None
        if len(item) != 3:
            return None
        agent_id, version, descriptor_hash = item
        if (
            not isinstance(agent_id, str)
            or not isinstance(version, str)
            or not isinstance(descriptor_hash, str)
        ):
            return None
        extracted.append((agent_id, version, descriptor_hash))

    return sorted(extracted, key=lambda x: (x[0], x[1]))


def _extract_runtime_accessed_fields(runtime_state_repr: str) -> list[str] | None:
    """
    New M4 adversarial mode: runtime_state_repr contains only observable
    access surface, not descriptor commitments.
    """
    try:
        payload = canonical_loads(runtime_state_repr)
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    accessed_fields_raw = payload.get("accessed_fields")
    if not isinstance(accessed_fields_raw, list):
        return None

    accessed: list[str] = []
    for v in accessed_fields_raw:
        if not isinstance(v, str):
            return None
        accessed.append(v)

    return sorted(set(accessed))


@dataclass
class MemoryBindingAuditEmitter(IBindingAuditEmitter):
    """
    Minimal deterministic M4 binding audit emitter.

    MVP behavior:
    - emit(record): stores the record in-memory (for tests / deterministic validation)
    - emit_equivalence_sweep(descriptors, runtime_state_repr):
        computes registry_hash + runtime_state_hash
        compares runtime descriptor commitments (if present) to registry commitments
        fallback: hash equality for non-structured runtime state representations
    """

    emitted: list[BindingAuditRecord] = field(default_factory=list)

    def emit(self, record: BindingAuditRecord) -> None:
        self.emitted.append(record)

    def emit_equivalence_sweep(
        self,
        descriptors: dict[tuple[str, str], ProjectionDescriptor],
        runtime_state_repr: str,
    ) -> BindingAuditRecord:
        registry_items = sorted(
            (
                agent_id,
                version,
                desc.canonical_hash(),
            )
            for (agent_id, version), desc in descriptors.items()
        )
        registry_hash = _compute_registry_hash(descriptors)
        runtime_state_hash = _compute_runtime_state_hash(runtime_state_repr)
        runtime_descriptor_items = _extract_runtime_descriptor_items(runtime_state_repr)

        if runtime_descriptor_items is not None:
            # Backward-compatible structured M4 mode (tests supply descriptors echo).
            equivalent = runtime_descriptor_items == registry_items
            divergence_details = (
                ""
                if equivalent
                else "runtime descriptors differ from registry descriptors"
            )
        else:
            # Adversarially meaningful M4 mode: runtime_state_repr is expected to
            # carry only the observed access surface (e.g., `accessed_fields`), not
            # descriptor commitments. We compare that against the declared
            # projection scopes' field surface.
            accessed_fields_raw = _extract_runtime_accessed_fields(runtime_state_repr)
            if accessed_fields_raw is None:
                equivalent = runtime_state_hash == registry_hash
                divergence_details = (
                    "" if equivalent else f"runtime_state_hash={runtime_state_hash}"
                )
            else:
                expected_accessed_fields = sorted(
                    {f.value for desc in descriptors.values() for f in desc.scope}
                )
                equivalent = accessed_fields_raw == expected_accessed_fields
                divergence_details = (
                    ""
                    if equivalent
                    else (
                        f"accessed_fields differ: accessed={accessed_fields_raw} expected={expected_accessed_fields}"
                    )
                )

        equivalence_status = "EQUIVALENT" if equivalent else "DIVERGED"

        record = BindingAuditRecord(
            registry_hash=registry_hash,
            runtime_state_hash=runtime_state_hash,
            equivalence_status=equivalence_status,
            divergence_details=divergence_details,
            agent_id="orchestration",
            binding_hook_id="M4",
        )
        self.emit(record)
        return record
