from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from egregore.interface.constraint_binding_ports import (
    CompositionGuardError,
    ICompositionGuard,
)
from egregore.shared.canonical import canonical_json, sha256_hex


def _stable_fingerprint(value: Any) -> str:
    """
    Deterministic fingerprint for CBI-0 composition guard.

    This is intentionally conservative: for dataclasses we use asdict(),
    otherwise we hash repr(value).
    """
    if dataclasses.is_dataclass(value):
        payload = dataclasses.asdict(value)
    elif isinstance(value, dict):
        payload = value
    else:
        payload = {"repr": repr(value)}
    return sha256_hex(canonical_json(payload).encode("utf-8"))


@dataclass
class CompositionGuard(ICompositionGuard):
    """
    Concrete CBI-0 M3 composition guard.

    Enforces:
    - assert_terminal: an already-terminal artifact cannot be re-entered/reused.
        - assert_no_implicit_ir_synthesis: when a known terminal artifact is routed toward
            CanonicalSemanticIR construction without an explicit bridge, fail closed.
    """

    _terminal_fingerprints: set[str] = field(default_factory=set)

    def assert_terminal(
        self,
        output: Any,
        source_agent_id: str,
    ) -> None:
        fp = _stable_fingerprint(output)
        if fp in self._terminal_fingerprints:
            raise CompositionGuardError(
                source_agent_id=source_agent_id,
                output_type=type(output).__name__,
                target_type="CanonicalSemanticIR",
            )
        self._terminal_fingerprints.add(fp)

    def assert_no_implicit_ir_synthesis(
        self,
        source_agent_id: str,
        target_input: Any,
        target_type_name: str,
    ) -> None:
        target_is_ir = (
            target_type_name == "CanonicalSemanticIR"
            or target_type_name.endswith(".CanonicalSemanticIR")
        )

        if not target_is_ir:
            return

        target_fp = _stable_fingerprint(target_input)
        known_terminal_type = type(target_input).__name__ in {"LegalAnalysisOutput"}
        known_terminal_fp = target_fp in self._terminal_fingerprints

        if known_terminal_type or known_terminal_fp:
            raise CompositionGuardError(
                source_agent_id=source_agent_id,
                output_type=type(target_input).__name__,
                target_type="CanonicalSemanticIR",
            )
