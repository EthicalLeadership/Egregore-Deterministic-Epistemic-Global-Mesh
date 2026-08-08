"""Integration-pipeline orchestrator.

Wires the governance triad together:

1. Manifest validation (``validate_manifest``)
2. M1/M2 governance checks (``run_m1`` / ``run_m2``)
3. Provenance signing (``sign_provenance``)

The orchestrator is intentionally thin: each step is independently testable,
and this module only handles loading, sequencing, and reporting.  Loading
failures are captured in the report rather than raised, so the pipeline can
be run against messy inputs without crashing.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from egregore.pipeline.governance import run_m1, run_m2
from egregore.pipeline.manifest_validator import validate_manifest
from egregore.pipeline.provenance_signer import sign_provenance
from egregore.shared.canonical import canonical_loads

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntegrationReport:
    """Result of running the integration pipeline on a module directory."""

    module_id: str
    manifest_valid: bool
    manifest_errors: list[dict[str, str]]
    m1_violations: list[dict[str, str]]
    m2_violations: list[dict[str, str]]
    provenance: dict[str, Any] | None
    load_errors: list[dict[str, str]] = field(default_factory=list)

    def is_pass(self) -> bool:
        """Return True only if the manifest is valid and there are no errors.

        Violations without an explicit ``severity`` are treated as errors to
        avoid silent passes on malformed checker output.
        """
        if not self.manifest_valid or self.load_errors:
            return False
        return not any(
            v.get("severity", "error") == "error"
            for v in self.m1_violations + self.m2_violations
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the report to a plain dict."""
        return {
            "module_id": self.module_id,
            "manifest_valid": self.manifest_valid,
            "manifest_errors": self.manifest_errors,
            "load_errors": self.load_errors,
            "m1_violations": self.m1_violations,
            "m2_violations": self.m2_violations,
            "provenance": self.provenance,
            "pass": self.is_pass(),
        }


class IntegrationPipeline:
    """Run validation, governance, and provenance signing for one module."""

    def __init__(
        self,
        *,
        plane1_ports: list[str],
        concrete_infrastructure: list[str],
        port_registry: list[str],
        signer_id: str = "egregore-pipeline",
        private_key: Ed25519PrivateKey | None = None,
    ) -> None:
        self.plane1_ports = plane1_ports
        self.concrete_infrastructure = concrete_infrastructure
        self.port_registry = port_registry
        self.signer_id = signer_id
        self.private_key = private_key

    def run(self, module_dir: Path | str) -> IntegrationReport:
        """Execute the full triad against *module_dir*.

        *module_dir* should contain a ``egregore-module.json`` file and one or
        more ``.py`` source files.  Missing manifests, invalid JSON, and source
        files that fail to parse are all reported as failures instead of
        raising exceptions.
        """
        module_path = Path(module_dir).resolve()
        manifest_path = module_path / "egregore-module.json"

        manifest, manifest_errors = _load_manifest(manifest_path)
        module_id = (
            manifest.get("name", module_path.name) if manifest else module_path.name
        )

        if manifest_errors:
            return IntegrationReport(
                module_id=module_id,
                manifest_valid=False,
                manifest_errors=manifest_errors,
                m1_violations=[],
                m2_violations=[],
                provenance=None,
            )

        # The manifest dict is guaranteed non-None here because manifest_errors
        # is populated when loading fails.
        assert manifest is not None  # noqa: S101

        validation_errors = validate_manifest(manifest)
        if validation_errors:
            return IntegrationReport(
                module_id=module_id,
                manifest_valid=False,
                manifest_errors=validation_errors,
                m1_violations=[],
                m2_violations=[],
                provenance=None,
            )

        source_asts, load_errors = _load_source_asts(module_path)
        if load_errors:
            return IntegrationReport(
                module_id=module_id,
                manifest_valid=True,
                manifest_errors=[],
                m1_violations=[],
                m2_violations=[],
                provenance=None,
                load_errors=load_errors,
            )

        m1_violations = run_m1(
            source_asts,
            manifest,
            self.plane1_ports,
            self.concrete_infrastructure,
        )
        m2_violations = run_m2(source_asts, manifest, self.port_registry)

        provenance: dict[str, Any] | None = None
        if self.private_key is not None:
            record = {
                "module_id": module_id,
                "manifest": manifest,
                "m1_violations": m1_violations,
                "m2_violations": m2_violations,
            }
            provenance = sign_provenance(record, self.private_key, self.signer_id)

        return IntegrationReport(
            module_id=module_id,
            manifest_valid=True,
            manifest_errors=[],
            m1_violations=m1_violations,
            m2_violations=m2_violations,
            provenance=provenance,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_manifest(
    manifest_path: Path,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Load ``egregore-module.json`` and return (manifest, errors).

    Errors are returned for a missing file or invalid JSON.  A successfully
    loaded manifest is returned with an empty error list.
    """
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, [
            {"field": "manifest", "message": "egregore-module.json not found"}
        ]

    try:
        manifest = canonical_loads(text)
    except json.JSONDecodeError as exc:
        return None, [{"field": "manifest", "message": f"Invalid JSON: {exc}"}]

    if not isinstance(manifest, dict):
        return None, [
            {"field": "manifest", "message": "Manifest root must be a JSON object"}
        ]

    return manifest, []


def _load_source_asts(
    module_dir: Path,
) -> tuple[list[ast.Module], list[dict[str, str]]]:
    """Parse all Python files under *module_dir* into AST trees.

    Returns a tuple of (asts, load_errors).  Syntax errors are captured as
    load errors instead of raising.
    """
    asts: list[ast.Module] = []
    load_errors: list[dict[str, str]] = []
    for path in sorted(module_dir.rglob("*.py")):
        if path.name.startswith("test_"):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            asts.append(ast.parse(source, filename=str(path)))
        except SyntaxError as exc:
            load_errors.append(
                {
                    "field": str(path.relative_to(module_dir)),
                    "message": f"Python syntax error: {exc.msg}",
                }
            )
    return asts, load_errors
