"""Checkers and scanners for the build-time CBI-0 module pipeline."""

from __future__ import annotations

import ast
import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from egregore.tooling.pipeline_models import (
    ALLOWED_LAYER_DEPENDENCIES,
    PLANE1_LAYERS,
    PLANE2_LAYERS,
    CheckResult,
    ModuleManifest,
    Violation,
    _load_cell_spec,
)

logger = logging.getLogger(__name__)

# Modules privileged to import Plane-1 ports directly from Plane 2.
ORCHESTRATOR_MODULES = {
    "egregore.application.cbi_0_orchestrated_executor",
    "egregore.interface.bootstrap",
    "egregore.application.legal_reasoning_engine",
}

# Known Plane-1 port modules. Plane-2 modules may import these only if explicitly
# privileged via ORCHESTRATOR_MODULES or the external plane1_ports.json registry.
PLANE1_PORT_MODULES = {
    "egregore.interface.constraint_binding_ports",
    "egregore.interface.factory_router",
    "egregore.interface.orchestration_ports",
    "egregore.interface.semantics_ports",
    "egregore.interface.provenance_port",
    "egregore.interface.zarc_journal_ports",
}

# Modules that indicate model/agent infrastructure usage for the M5 stub.
MODEL_AGENT_MODULES = {
    "egregore.infrastructure.local_model_catalog",
    "egregore.infrastructure.gguf_catalog",
    "egregore.infrastructure.local_model_client",
    "egregore.application.agent_registry",
    "egregore.application.agent_runner",
    "egregore.application.chat_interpreter",
}

# Capability patterns for AST + regex heuristics.
CAPABILITY_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "read": [
        ("call", r"\bopen\s*\("),
        ("import", r"^pathlib$"),
        ("import", r"^os\.path$"),
    ],
    "write": [
        ("call", r"\bopen\s*\([^)]*,\s*['\"]w"),
        ("call", r"\.write_text\s*\("),
        ("call", r"\.write_bytes\s*\("),
    ],
    "execute": [
        ("import", r"^subprocess$"),
        ("call", r"\bsubprocess\.run\s*\("),
        ("call", r"\bsubprocess\.Popen\s*\("),
    ],
    "network": [
        ("import", r"^requests$"),
        ("import", r"^httpx$"),
        ("import", r"^urllib"),
        ("import", r"^http"),
        ("call", r"\brequests\."),
        ("call", r"\bhttpx\."),
    ],
}


def _module_path_to_name(rel_path: Path) -> str:
    """Convert src/egregore/foo/bar.py -> egregore.foo.bar."""
    parts = list(rel_path.with_suffix("").parts)
    return ".".join(parts)


def _resolve_imported_module(import_name: str) -> str | None:
    """Return the top-level egregore module name for an import, or None."""
    if not import_name.startswith("egregore."):
        return None
    parts = import_name.split(".")
    if len(parts) < 2:
        return None
    return ".".join(parts[:2])


class PlaneLayerClassifier:
    def classify(self, module_name: str) -> tuple[str, str]:
        """Return (plane, layer) for a fully qualified module name."""
        parts = module_name.split(".")
        if len(parts) < 2:
            return ("unknown", "unknown")
        layer = parts[1]
        if layer in PLANE1_LAYERS:
            return ("plane1", layer)
        if layer in PLANE2_LAYERS:
            return ("plane2", layer)
        return ("shared", layer)


class AstImportAnalyzer:
    def __init__(self, source: str, module_name: str) -> None:
        self.source = source
        self.tree = ast.parse(source)
        self.module_name = module_name

    def _under_type_checking(
        self, node: ast.AST, parents: dict[ast.AST, ast.AST]
    ) -> bool:
        current: ast.AST | None = node
        while current is not None:
            parent = parents.get(current)
            if (
                isinstance(parent, ast.If)
                and isinstance(parent.test, ast.Name)
                and parent.test.id == "TYPE_CHECKING"
            ):
                return True
            current = parent
        return False

    def imports(self) -> set[str]:
        """Return all imported top-level module names.

        Imports guarded by ``if TYPE_CHECKING:`` are omitted because they do not
        represent runtime dependencies.
        """
        found: set[str] = set()
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(self.tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                if self._under_type_checking(node, parents):
                    continue
                for alias in node.names:
                    found.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                if self._under_type_checking(node, parents):
                    continue
                found.add(node.module)
        return found

    def egregore_imports(self) -> set[str]:
        """Return top-level egregore modules imported by this file."""
        result: set[str] = set()
        for imp in self.imports():
            mod = _resolve_imported_module(imp)
            if mod and mod != self.module_name.split(".")[0]:
                result.add(mod)
        return result

    def resolves_to(self, module_prefix: str) -> bool:
        """Check whether any import starts with the given prefix."""
        for imp in self.imports():
            if imp == module_prefix or imp.startswith(module_prefix + "."):
                return True
        return False


class CapabilityScanner:
    def scan(self, source: str) -> dict[str, list[str]]:
        """Return detected capability usages keyed by capability name."""
        detected: dict[str, list[str]] = {name: [] for name in CAPABILITY_PATTERNS}
        lines = source.splitlines()
        for line_no, line in enumerate(lines, start=1):
            for cap_name, patterns in CAPABILITY_PATTERNS.items():
                for _kind, pattern in patterns:
                    if re.search(pattern, line):
                        entry = f"line {line_no}: {line.strip()[:80]}"
                        if entry not in detected[cap_name]:
                            detected[cap_name].append(entry)
                        break
        return detected


class DeterminismScanner:
    """Detect non-deterministic constructs in Python source."""

    PATTERNS: list[tuple[str, str]] = [
        (
            r"\brandom\.(random|randint|choice|shuffle|sample|uniform)\s*\(",
            "unseeded random usage",
        ),
        (r"\bdatetime\.(?:datetime\.)?now\s*\(", "wall-clock datetime"),
        (r"\bdatetime\.(?:datetime\.)?today\s*\(", "wall-clock datetime"),
        (r"\btime\.time\s*\(", "wall-clock time"),
    ]

    def scan(self, source: str, file_path: Path) -> list[Violation]:
        violations: list[Violation] = []
        for line_no, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pattern, description in self.PATTERNS:
                if re.search(pattern, line):
                    if "seed" in line:
                        continue
                    violations.append(
                        Violation(
                            "M5",
                            "M5-DET",
                            f"{file_path}:{line_no}: {description}",
                        )
                    )
        return violations


class EpistemicScanner:
    """Detect whether a module or cell spec shows evidence of epistemic hygiene."""

    MARKERS = {
        "provenance",
        "source",
        "evidence",
        "citation",
        "disclaimer",
        "attribution",
        "auditability",
    }

    def scan_source(self, source: str, file_path: Path) -> list[Violation]:
        """Return a violation if the source lacks any epistemic marker."""
        lowered = source.lower()
        if any(marker in lowered for marker in self.MARKERS):
            return []
        return [
            Violation(
                "M5",
                "M5-EPI",
                f"{file_path}: no epistemic marker found (provenance/source/evidence/citation/disclaimer/attribution/auditability).",
            )
        ]

    def scan_cell_spec(self, spec: dict[str, Any]) -> list[Violation]:
        """Return violations if the cell spec lacks verification or moral rules."""
        violations: list[Violation] = []
        verification = spec.get("verification") or {}
        moral = spec.get("moral_compliance") or {}
        if not verification.get("rules"):
            violations.append(
                Violation(
                    "M5",
                    "M5-EPI",
                    f"cell {spec.get('cell_id')}: spec has no verification.rules.",
                )
            )
        if not moral.get("egregore_laws"):
            violations.append(
                Violation(
                    "M5",
                    "M5-EPI",
                    f"cell {spec.get('cell_id')}: spec has no moral_compliance.egregore_laws.",
                )
            )
        return violations


class M1Checker:
    """Enforce the approved cross-layer dependency matrix (M1)."""

    VIOLATION_LAYER = "M1-LAYER"
    VIOLATION_IFACE = "M1-IFACE"

    # Per-file allowlists that mirror tests/test_arch_enforcement.py.  These
    # document known architectural debt; new files must not rely on them.
    ALLOWED_APPLICATION_INFRASTRUCTURE_IMPORTS = {
        "egregore/application/pressure_controller.py",
        "egregore/application/capacity_orchestrator.py",
        "egregore/application/distributed_scheduler.py",
        "egregore/application/container.py",
        "egregore/application/placement_policy.py",
        "egregore/application/inference_service.py",
        "egregore/application/chat_inference_orchestrator.py",
        "egregore/application/chat_interpreter.py",
    }

    ALLOWED_KERNEL_DOMAIN_IMPORTS = {
        "egregore/kernel/scheduler/dt_monitor.py",
        "egregore/kernel/scheduler/epoch_scheduler.py",
        "egregore/kernel/scheduler/powertrain_coupling.py",
        "egregore/kernel/scheduler/tu_budget.py",
    }

    ALLOWED_INTERFACE_GOVERNANCE_IMPORTS = {
        "egregore/interface/ombudsman_router.py",
    }

    def __init__(
        self, classifier: PlaneLayerClassifier, plane1_ports: dict[str, Any]
    ) -> None:
        self.classifier = classifier
        # plane1_ports is retained for API compatibility but the matrix is the
        # single source of truth for layer dependency decisions.
        self.plane1_ports = plane1_ports

    def _is_allowed(
        self,
        layer: str,
        imp_layer: str,
        rel_path: str,
    ) -> bool:
        if layer == "application" and imp_layer == "infrastructure":
            return rel_path in self.ALLOWED_APPLICATION_INFRASTRUCTURE_IMPORTS
        if layer == "kernel" and imp_layer == "domain":
            return rel_path in self.ALLOWED_KERNEL_DOMAIN_IMPORTS
        if layer == "interface" and imp_layer == "governance":
            return rel_path in self.ALLOWED_INTERFACE_GOVERNANCE_IMPORTS
        return False

    def check(  # noqa: C901
        self,
        module_name: str,
        layer: str,
        imports: set[str],
        file_imports: dict[Path, set[str]] | None = None,
        src_root: Path | None = None,
    ) -> CheckResult:
        violations: list[Violation] = []

        if layer not in ALLOWED_LAYER_DEPENDENCIES:
            violations.append(
                Violation(
                    "M1",
                    self.VIOLATION_LAYER,
                    f"module {module_name} has unknown layer {layer!r}.",
                )
            )
            return CheckResult("FAIL", violations)

        allowed = ALLOWED_LAYER_DEPENDENCIES[layer]

        def _check_imp(rel_path: str, imp: str) -> None:
            if not imp.startswith("egregore."):
                return
            imp_layer = self.classifier.classify(imp)[1]
            if imp_layer == layer:
                return
            if imp_layer not in ALLOWED_LAYER_DEPENDENCIES:
                # Unknown target layer (e.g. ephemeral test packages) cannot be
                # judged by the production matrix; rely on M2 graph checks.
                return
            # Interface may never depend on concrete infrastructure.
            if layer == "interface" and imp_layer == "infrastructure":
                violations.append(
                    Violation(
                        "M1",
                        self.VIOLATION_IFACE,
                        f"{rel_path} imports {imp} — Interface layer may not import concrete infrastructure.",
                    )
                )
                return
            if imp_layer in allowed:
                return
            if self._is_allowed(layer, imp_layer, rel_path):
                return
            violations.append(
                Violation(
                    "M1",
                    self.VIOLATION_LAYER,
                    f"{rel_path} imports {imp} (layer={layer}, allowed={sorted(allowed)}).",
                )
            )

        if file_imports:
            for file_path, file_imps in sorted(file_imports.items()):
                rel_path = (
                    file_path.relative_to(src_root).as_posix()
                    if src_root
                    else file_path.as_posix()
                )
                for imp in sorted(file_imps):
                    _check_imp(rel_path, imp)
        else:
            # Aggregate fallback (used by unit tests).
            for imp in sorted(imports):
                _check_imp(f"module {module_name}", imp)

        status = "PASS" if not violations else "FAIL"
        return CheckResult(status, violations)


class M2Checker:
    VIOLATION_DEP = "M2-DEP"
    VIOLATION_DEP_FLOAT = "M2-DEP-FLOAT"
    VIOLATION_CAP = "M2-CAP"
    VIOLATION_PORT = "M2-PORT"

    def __init__(self, project_version: str) -> None:
        self.project_version = project_version

    def _hash_source(self, module_name: str) -> str | None:
        """Compute SHA-256 of the imported module's source file if available."""
        if not module_name.startswith("egregore."):
            return None
        parts = module_name.split(".")[1:]
        rel_path = Path(*parts)
        candidates = [
            Path(__file__).resolve().parents[2]
            / "egregore"
            / (rel_path.with_suffix(".py")),
            Path(__file__).resolve().parents[2] / "egregore" / rel_path / "__init__.py",
        ]
        for candidate in candidates:
            if candidate.exists():
                return hashlib.sha256(candidate.read_bytes()).hexdigest()
        return None

    def _check_dependencies(
        self, module_name: str, imports: set[str], manifest: ModuleManifest
    ) -> list[Violation]:
        violations: list[Violation] = []
        declared_deps = {d.get("module") for d in manifest.cbi0.m2_dependencies}
        for imp in sorted(imports):
            if not imp.startswith("egregore.") or imp == module_name:
                continue
            if imp not in declared_deps:
                violations.append(
                    Violation(
                        "M2",
                        self.VIOLATION_DEP,
                        f"imported Egregore module {imp} is not declared in manifest dependencies.",
                    )
                )
                continue
            dep = next(
                d for d in manifest.cbi0.m2_dependencies if d.get("module") == imp
            )
            version = dep.get("version", "")
            if version and re.search(r"[<>=~*]|latest|HEAD", version):
                violations.append(
                    Violation(
                        "M2",
                        self.VIOLATION_DEP_FLOAT,
                        f"dependency {imp} uses floating version {version!r}.",
                    )
                )
            if not dep.get("hash"):
                violations.append(
                    Violation(
                        "M2",
                        self.VIOLATION_DEP,
                        f"dependency {imp} is missing a hash.",
                    )
                )
        return violations

    def _check_capabilities(
        self, capabilities: dict[str, list[str]], manifest: ModuleManifest
    ) -> list[Violation]:
        violations: list[Violation] = []
        declared_caps = manifest.cbi0.m2_capabilities.model_dump()
        for cap_name, usages in capabilities.items():
            declared = declared_caps.get(cap_name, [])
            if usages and not declared:
                violations.append(
                    Violation(
                        "M2",
                        self.VIOLATION_CAP,
                        f"capability '{cap_name}' used but not declared in manifest ({len(usages)} occurrences).",
                    )
                )
        return violations

    def _check_ports(self, manifest: ModuleManifest) -> list[Violation]:
        violations: list[Violation] = []
        for port in manifest.cbi0.m2_ports.implements:
            if not port.startswith("I"):
                violations.append(
                    Violation(
                        "M2",
                        self.VIOLATION_PORT,
                        f"implemented port {port!r} does not follow interface naming convention.",
                    )
                )
        return violations

    def check(
        self,
        module_name: str,
        layer: str,
        imports: set[str],
        capabilities: dict[str, list[str]],
        manifest: ModuleManifest,
    ) -> CheckResult:
        violations: list[Violation] = []
        violations.extend(self._check_dependencies(module_name, imports, manifest))
        violations.extend(self._check_capabilities(capabilities, manifest))
        violations.extend(self._check_ports(manifest))

        hard_fail = manifest.cbi0.m1_plane == "plane1"
        status = (
            "FAIL"
            if (hard_fail and violations)
            else ("PASS" if not violations else "WARN")
        )
        return CheckResult(status, violations)


class M3Checker:
    """M3 — Terminal Non-Reentry gate.

    Modules declared ``terminal=True`` in their manifest assert that they are
    non-reentrant and have a certified decommissioning plan. This checker is
    a structural lock: a terminal module must provide a ``DecomManifest`` with
    a valid ``Attestation`` (signed by the Dependency Safety Board or covered
    by a documented bootstrap waiver). Cascade-prone teardown patterns
    (``__del__`` destructors and ``atexit`` hooks) are reported as warnings
    but do not block the build once the attestation is present.
    """

    VIOLATION_TERMINAL = "M3-TERM"
    VIOLATION_NO_DECOM = "M3-NO-DECOM"
    VIOLATION_NO_ATTESTATION = "M3-NO-ATTESTATION"

    # Patterns that indicate a module may resist clean, deterministic
    # decommissioning. These are heuristics, not a full static analysis.
    CASCADE_PATTERNS: list[tuple[str, str]] = [
        (r"\bdef\s+__del__\s*\(", "__del__ destructor"),
        (r"\bimport\s+atexit\b", "atexit import"),
        (r"\batexit\.register\s*\(", "atexit.register call"),
    ]

    def _scan_source(self, source: str, file_path: Path) -> list[Violation]:
        violations: list[Violation] = []
        for line_no, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pattern, description in self.CASCADE_PATTERNS:
                if re.search(pattern, line):
                    violations.append(
                        Violation(
                            "M3",
                            self.VIOLATION_TERMINAL,
                            f"{file_path}:{line_no}: terminal module contains "
                            f"{description}.",
                        )
                    )
                    break
        return violations

    @staticmethod
    def _has_valid_attestation(decom_manifest: Any) -> bool:
        """Return True if the manifest carries a signed or waived attestation."""
        if decom_manifest is None or decom_manifest.attestation is None:
            return False
        attestation = decom_manifest.attestation
        # Signed DSB attestation requires all three fields.
        signed = bool(
            attestation.signature and attestation.signer_id and attestation.timestamp
        )
        # Bootstrap waiver is a controlled, public, temporary placeholder.
        waived = bool(attestation.bootstrap_waiver)
        return signed or waived

    def check(
        self,
        sources: list[tuple[Path, str]],
        manifest: ModuleManifest,
    ) -> CheckResult:
        terminal = manifest.cbi0.m3.terminal
        if not terminal:
            return CheckResult(
                "NOT_ENFORCED",
                [],
                {"terminal": False, "note": "Module is not declared terminal."},
            )

        violations: list[Violation] = []
        decom_manifest = manifest.cbi0.m3.decom_manifest

        if decom_manifest is None:
            violations.append(
                Violation(
                    "M3",
                    self.VIOLATION_NO_DECOM,
                    "Terminal module must include a decom manifest.",
                )
            )
        elif not self._has_valid_attestation(decom_manifest):
            violations.append(
                Violation(
                    "M3",
                    self.VIOLATION_NO_ATTESTATION,
                    "Terminal module requires a signed decommissioning attestation "
                    "or a valid bootstrap waiver.",
                )
            )

        # Cascade-pattern scan is secondary: it warns even when the manifest is
        # properly attested, so maintainers cannot hide risky teardown code.
        for file_path, source in sources:
            violations.extend(self._scan_source(source, file_path))

        status = (
            "FAIL"
            if any(
                v.rule in {self.VIOLATION_NO_DECOM, self.VIOLATION_NO_ATTESTATION}
                for v in violations
            )
            else ("WARN" if violations else "PASS")
        )
        metadata = {
            "terminal": True,
            "attested": (
                self._has_valid_attestation(decom_manifest) if decom_manifest else False
            ),
            "note": "Terminal non-reentry posture declared.",
        }
        return CheckResult(status, violations, metadata)


class M5CellAwarenessChecker:
    """M5: cell-awareness gate for modules that touch model/agent infrastructure."""

    VIOLATION_CELL = "M5-CELL"

    def __init__(self) -> None:
        self.determinism_scanner = DeterminismScanner()
        self.epistemic_scanner = EpistemicScanner()

    def _uses_model_agent(self, imports: set[str]) -> bool:
        return any(
            imp == prefix or imp.startswith(prefix + ".")
            for imp in imports
            for prefix in MODEL_AGENT_MODULES
        )

    def _check_cell_determinism(self, spec: dict[str, Any]) -> list[Violation]:
        violations: list[Violation] = []
        cell_id = spec.get("cell_id")
        for stage in spec.get("pipeline", {}).get("stages", []):
            if stage.get("model") is not None and stage.get("temperature", 0.0) != 0.0:
                violations.append(
                    Violation(
                        "M5",
                        "M5-DET",
                        f"cell {cell_id} stage {stage.get('stage_id')} uses non-zero temperature {stage['temperature']}.",
                    )
                )
        return violations

    def check(
        self,
        imports: set[str],
        sources: list[tuple[Path, str]],
        manifest: ModuleManifest,
    ) -> CheckResult:
        if not self._uses_model_agent(imports):
            return CheckResult(
                "NOT_ENFORCED",
                [],
                {"note": "No model/agent infrastructure usage detected."},
            )

        violations: list[Violation] = []
        metadata: dict[str, Any] = {"cell": manifest.cell}
        spec: dict[str, Any] | None = None

        if manifest.cell:
            spec = _load_cell_spec(manifest.cell)
            if spec is None:
                violations.append(
                    Violation(
                        "M5",
                        self.VIOLATION_CELL,
                        f"Declared cell {manifest.cell!r} has no spec.yaml.",
                    )
                )
            else:
                violations.extend(self._check_cell_determinism(spec))
                violations.extend(self.epistemic_scanner.scan_cell_spec(spec))
        else:
            violations.append(
                Violation(
                    "M5",
                    self.VIOLATION_CELL,
                    "Module uses model/agent infrastructure without a declared cell.",
                )
            )

        for file_path, source in sources:
            violations.extend(self.determinism_scanner.scan(source, file_path))
            if spec is None:
                violations.extend(self.epistemic_scanner.scan_source(source, file_path))

        if violations:
            return CheckResult("WARN", violations, metadata)
        return CheckResult("PASS", [], metadata)


# Backward-compatible alias for code that imported the old stub name.
M5StubChecker = M5CellAwarenessChecker
