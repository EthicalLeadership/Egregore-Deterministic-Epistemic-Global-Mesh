from __future__ import annotations

from dataclasses import dataclass, field

from egregore.domain.semantics.projection_descriptor import (
    IRField,
    ProjectionDescriptor,
)
from egregore.interface.constraint_binding_ports import (
    IProjectionAccessMonitor,
    ProjectionBindingError,
    RegistryValidationError,
)


@dataclass
class ProjectionAccessMonitor(IProjectionAccessMonitor):
    """
    Concrete CBI-0 M1 enforcement surface.

    This monitor is intentionally minimal and deterministic:
    - M1 input is explicitly provided (accessed_fields)
    - declared scopes come only from IProjectionAccessMonitor.declare()
    - enforcement fails closed by raising ProjectionBindingError
    """

    _declared: dict[tuple[str, str], ProjectionDescriptor] = field(default_factory=dict)

    def snapshot_declared(self) -> dict[tuple[str, str], ProjectionDescriptor]:
        return dict(self._declared)

    def declare(
        self,
        agent_id: str,
        version: str,
        descriptor: ProjectionDescriptor,
    ) -> None:
        self._declared[(agent_id, version)] = descriptor

    def validate_access(
        self,
        agent_id: str,
        version: str,
        accessed_fields: frozenset[IRField],
    ) -> None:
        key = (agent_id, version)
        descriptor = self._declared.get(key)
        if descriptor is None:
            raise RegistryValidationError(
                f"No declared projection descriptor for agent {agent_id!r} version {version!r}"
            )

        undeclared = accessed_fields - descriptor.scope
        if undeclared:
            raise ProjectionBindingError(
                agent_id=agent_id,
                version=version,
                undeclared_fields=undeclared,
                declared_scope=descriptor.scope,
            )
