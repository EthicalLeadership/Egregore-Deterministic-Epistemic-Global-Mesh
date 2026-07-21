"""Pydantic models for the build-time CBI-0 module pipeline."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Optional dependency: PyYAML has no PEP 561 stubs.
import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator

SRC_ROOT = Path(__file__).resolve().parents[2]
PKG_ROOT = SRC_ROOT / "egregore"
PROJECT_ROOT = SRC_ROOT.parent
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"

PLANE1_LAYERS = {"kernel", "domain", "application", "governance"}
PLANE2_LAYERS = {
    "interface",
    "infrastructure",
    "http_api",
    "bus",
    "cortex",
    "dt1",
    "powertrain",
}
CROSS_LAYERS = {
    "shared",
    "models",
    "cells",
    "tooling",
    "cli",
    "patterns",
    "rfe",
    "services",
    "dossiers",
}

# Layer dependency matrix.  This is the single source of truth for M1 and is
# kept in sync with the architecture-policy tests.
ALLOWED_LAYER_DEPENDENCIES: dict[str, set[str]] = {
    "aegis_hive": {"cells"},
    "application": {
        "domain",
        "governance",
        "http_api",
        "interface",
        "infrastructure",
        "kernel",
        "models",
        "powertrain",
        "services",
        "shared",
    },
    "bus": set(),
    "cells": {"governance", "kernel", "shared", "tooling"},
    "cli": {"infrastructure", "models", "shared"},
    "cortex": {"shared"},
    "dossiers": set(),
    "domain": {"interface", "shared"},
    "dt1": set(),
    "governance": {"infrastructure", "models", "shared"},
    "patterns": {"domain", "shared"},
    "pipeline": {"shared"},
    "infrastructure": {
        "application",
        "domain",
        "interface",
        "kernel",
        "models",
        "shared",
    },
    "interface": {
        "application",
        "cells",
        "domain",
        "governance",
        "http_api",
        "models",
        "rfe",
        "shared",
    },
    "http_api": {
        "application",
        "domain",
        "governance",
        "infrastructure",
        "interface",
        "models",
        "rfe",
        "shared",
    },
    "kernel": {"domain", "shared"},
    "models": {"shared"},
    "powertrain": {"application", "domain", "infrastructure", "kernel"},
    "rfe": {"application", "kernel", "shared", "tooling"},
    "shared": {"domain"},
    "services": {
        "application",
        "domain",
        "infrastructure",
        "interface",
        "kernel",
        "shared",
    },
    "tooling": {"application", "shared"},
}


class CapabilityBlock(BaseModel):
    read: list[str] = Field(default_factory=list)
    write: list[str] = Field(default_factory=list)
    execute: list[str] = Field(default_factory=list)
    network: list[str] = Field(default_factory=list)


class PortBlock(BaseModel):
    implements: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)


class Attestation(BaseModel):
    """Cryptographic or bootstrap attestation for a decommissioning plan.

    In normal operation the plan is signed by the Dependency Safety Board
    (DSB). During bootstrap, a documented bootstrap waiver may be used
    instead. The waiver must be a public, revocable token recorded in the
    governance transparency log.
    """

    model_config = ConfigDict(extra="forbid")

    signature: str | None = Field(default=None)
    signer_id: str | None = Field(default=None)
    timestamp: str | None = Field(default=None)
    bootstrap_waiver: str | None = Field(
        default=None,
        pattern=r"^BOOTSTRAP-\d{4,}-\d{3,}$",
        description="Bootstrap waiver token, e.g. BOOTSTRAP-2026-001.",
    )


class DecomManifest(BaseModel):
    """M3 decommissioning manifest for terminal modules.

    Describes how the module can be safely undeployed without fracturing
    downstream systems. The manifest must be accompanied by a signed
    attestation or a bootstrap waiver.
    """

    model_config = ConfigDict(extra="forbid")

    dependencies: list[str] = Field(
        default_factory=list,
        description="External systems that depend on this module.",
    )
    procedure: str | None = Field(
        default=None,
        description="Repository-relative path to the decommissioning procedure document.",
    )
    test_log: str | None = Field(
        default=None,
        description="Repository-relative path to signed test output proving the procedure.",
    )
    attestation: Attestation | None = Field(default=None)


class M3Block(BaseModel):
    """M3 — Terminal Non-Reentry declaration.

    A terminal module is one whose undeployment could fracture downstream
    systems. Declaring ``terminal=True`` asserts that the module has a
    certified decommissioning plan and that the implementation avoids
    cascade-prone teardown patterns such as ``__del__`` destructors and
    ``atexit`` hooks.
    """

    model_config = ConfigDict(extra="forbid")

    terminal: bool = Field(
        default=False,
        description="True when the module is terminal/non-reentrant.",
    )
    decom_manifest: DecomManifest | None = Field(default=None)


class Cbi0Block(BaseModel):
    model_config = ConfigDict(extra="forbid")

    m1_plane: str = Field(default="plane1", pattern=r"^(plane1|plane2|shared)$")
    m1_layer: str = Field(default="application")
    m1_interface_concrete: bool = Field(default=False)
    m2_dependencies: list[dict[str, str]] = Field(default_factory=list)
    m2_capabilities: CapabilityBlock = Field(default_factory=CapabilityBlock)
    m2_ports: PortBlock = Field(default_factory=PortBlock)
    m3: M3Block = Field(default_factory=M3Block)
    m5_cell_aware: bool = Field(default=False)

    @field_validator("m1_layer")
    @classmethod
    def _valid_layer(cls, value: str) -> str:
        if value not in ALLOWED_LAYER_DEPENDENCIES:
            raise ValueError(f"unknown layer: {value!r}")
        return value


class ModuleManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    module_id: str
    version: str = "0.0.0"
    cell: str | None = Field(default=None)
    cbi0: Cbi0Block = Field(default_factory=Cbi0Block)

    @field_validator("cell")
    @classmethod
    def _known_cell(cls, value: str | None) -> str | None:
        if value is None:
            return value
        known = _load_cell_ids()
        if value not in known:
            raise ValueError(f"unknown cell: {value!r}; known cells: {sorted(known)}")
        return value


class AuditReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    module_id: str
    timestamp_ns: int
    pipeline_class: str
    m1: dict[str, Any]
    m2: dict[str, Any]
    m3: dict[str, Any]
    m4: dict[str, Any]
    m5: dict[str, Any]


@dataclass
class Violation:
    checkpoint: str
    rule: str
    detail: str


@dataclass
class CheckResult:
    status: str
    violations: list[Violation] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "violations": [
                {"checkpoint": v.checkpoint, "rule": v.rule, "detail": v.detail}
                for v in self.violations
            ],
            "metadata": self.metadata,
        }


def _load_cell_ids() -> set[str]:
    """Load cell identifiers from cells/*/spec.yaml on disk."""
    cells_dir = PROJECT_ROOT / "cells"
    if not cells_dir.exists():
        return set()
    ids: set[str] = set()
    for spec_path in sorted(cells_dir.glob("*/spec.yaml")):
        with contextlib.suppress(Exception):
            raw = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("cell_id"):
                ids.add(str(raw["cell_id"]))
    return ids


def _load_cell_spec(cell_id: str) -> dict[str, Any] | None:
    """Load a raw cell spec dict by cell identifier, or None if missing."""
    cells_dir = PROJECT_ROOT / "cells"
    spec_path = cells_dir / cell_id / "spec.yaml"
    if not spec_path.exists():
        return None
    try:
        # Compatibility: yaml.safe_load return type is untyped.
        return yaml.safe_load(spec_path.read_text(encoding="utf-8")) or None  # type: ignore[return-value]
    except Exception:
        return None


def _load_project_version() -> str:
    """Read the project version from pyproject.toml as a coarse dependency version."""
    import re

    with contextlib.suppress(Exception):
        text = PYPROJECT_PATH.read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        if match:
            return match.group(1)
    return "0.0.0"
