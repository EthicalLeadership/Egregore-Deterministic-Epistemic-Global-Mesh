"""Governance checks M1 and M2 for the Egregore integration pipeline.

M1: Projection access enforcement (plane/layer import boundaries).
M2: Registry completeness (dependency pinning, capability manifest, port registry).

All checks are fail-closed; warnings are used only where the governance record
requires a non-blocking observation (e.g., Plane-2 capability mismatches).
"""

from __future__ import annotations

import ast
from typing import Any

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_m1(
    source_asts: list[ast.Module],
    manifest: dict[str, Any],
    plane1_ports: list[str],
    concrete_infrastructure: list[str],
) -> list[dict[str, str]]:
    """Enforce plane/layer import boundaries.

    Args:
        source_asts: AST trees of all source files of the module.
        manifest: The module's egregore-module.json as a dict.
        plane1_ports: Fully qualified names of Plane-1 modules allowed for
            Plane-2 imports (e.g., ["plane1.ports.ITelemetryPublisher"]).
        concrete_infrastructure: Module names that are concrete infrastructure
            and must not be imported by the Interface layer.

    Returns:
        A list of violation dicts with keys rule_id, message, severity.

    """
    violations: list[dict[str, str]] = []

    module_plane = _require(manifest, "plane", "manifest")
    module_layer = _require(manifest, "layer", "manifest")

    all_imports = _collect_imports(source_asts)

    for imp_full, _imp_components in all_imports:
        imp_plane = _plane_from_name(imp_full)
        if imp_plane is None:
            continue  # Not a Egregore internal import

        # M1-P1: Plane-1 must not import Plane-2
        if module_plane == "plane1" and imp_plane == "plane2":
            violations.append(
                _violation(
                    "M1-P1",
                    f"Plane-1 module may not import Plane-2 module '{imp_full}'.",
                    "error",
                )
            )

        # M1-P2-INT: Plane-2 may only import Plane-1 through declared ports
        if (
            module_plane == "plane2"
            and imp_plane == "plane1"
            and imp_full not in plane1_ports
        ):
            violations.append(
                _violation(
                    "M1-P2-INT",
                    f"Plane-2 module imports '{imp_full}' which is not a "
                    f"declared Plane-1 port.",
                    "error",
                )
            )

        # M1-IFACE: Interface layer must not import concrete infrastructure
        if module_layer == "interface" and imp_full in concrete_infrastructure:
            violations.append(
                _violation(
                    "M1-IFACE",
                    f"Interface layer module may not import concrete "
                    f"infrastructure '{imp_full}'.",
                    "error",
                )
            )

    return violations


def run_m2(  # noqa: C901
    source_asts: list[ast.Module],
    manifest: dict[str, Any],
    port_registry: list[str],
) -> list[dict[str, str]]:
    """Enforce registry completeness rules.

    Args:
        source_asts: AST trees of all source files of the module.
        manifest: The module's egregore-module.json as a dict.
        port_registry: List of known port names (interface names).

    Returns:
        A list of violation dicts with keys rule_id, message, severity.

    """
    violations: list[dict[str, str]] = []

    module_plane = _require(manifest, "plane", "manifest")
    declared_deps: dict[str, dict[str, str]] = _index_dependencies(manifest)

    # ----- M2-DEP & M2-DEP-FLOAT: dependency pinning -----
    all_imports = _collect_imports(source_asts)
    imported_internal = {
        imp for imp, _ in all_imports if _plane_from_name(imp) is not None
    }

    for imp in sorted(imported_internal):
        dep_entry = declared_deps.get(imp)
        if dep_entry is None:
            violations.append(
                _violation(
                    "M2-DEP",
                    f"Internal import '{imp}' is not declared in manifest dependencies.",
                    "error",
                )
            )
            continue

        version = dep_entry.get("version", "")
        hash_val = dep_entry.get("hash", "")

        if not version or version == "latest" or not _is_pinned(version):
            violations.append(
                _violation(
                    "M2-DEP-FLOAT",
                    f"Dependency '{imp}' has a floating or missing version: '{version}'.",
                    "error",
                )
            )

        if not hash_val or len(hash_val) < 64:
            violations.append(
                _violation(
                    "M2-DEP-FLOAT",
                    f"Dependency '{imp}' has a missing or suspicious hash: '{hash_val}'.",
                    "error",
                )
            )

    # ----- M2-CAP: capability manifest coverage -----
    required_caps = _detect_required_capabilities(source_asts)
    declared_caps = _normalize_capabilities(manifest.get("capabilities", {}))
    missing_caps = required_caps - declared_caps

    if missing_caps:
        severity = "error" if module_plane == "plane1" else "warning"
        for cap in sorted(missing_caps):
            violations.append(
                _violation(
                    "M2-CAP",
                    f"Source uses capability '{cap}' but it is not declared in manifest.",
                    severity,
                )
            )

    # ----- M2-PORT: port registry check -----
    implements = manifest.get("ports", {}).get("implements", [])
    requires = manifest.get("ports", {}).get("requires", [])

    for port_name in implements + requires:
        if port_name not in port_registry:
            violations.append(
                _violation(
                    "M2-PORT",
                    f"Port '{port_name}' is not a known interface in the port registry.",
                    "error",
                )
            )

    # Overlap detection (M2-PORT overlap) would require external state;
    # logged as a placeholder warning if we cannot verify.
    if implements:
        violations.append(
            _violation(
                "M2-PORT",
                "Port overlap check not performed (requires live registry); "
                "manual audit required.",
                "warning",
            )
        )

    return violations


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _violation(rule_id: str, message: str, severity: str) -> dict[str, str]:
    return {"rule_id": rule_id, "message": message, "severity": severity}


def _require(data: dict[str, Any], key: str, context: str) -> str:
    val = data.get(key)
    if not val:
        raise ValueError(f"Missing required field '{key}' in {context}")
    return val


def _plane_from_name(full_module_name: str) -> str | None:
    """Return 'plane1', 'plane2', or None for non-Egregore modules."""
    if full_module_name.startswith("plane1."):
        return "plane1"
    if full_module_name.startswith("plane2."):
        return "plane2"
    return None


def _collect_imports(
    asts: list[ast.Module],
) -> list[tuple[str, list[str]]]:
    """Extract fully qualified module names from import statements."""
    imports: list[tuple[str, list[str]]] = []
    for tree in asts:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append((alias.name, alias.name.split(".")))
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:  # Absolute import
                    if node.module is None:
                        continue
                    for alias in node.names:
                        full = f"{node.module}.{alias.name}"
                        imports.append((full, full.split(".")))
                else:
                    # Relative imports skipped for now
                    continue
    return imports


def _index_dependencies(
    manifest: dict[str, Any],
) -> dict[str, dict[str, str]]:
    """Return a mapping of dependency name -> {version, hash}."""
    deps: dict[str, dict[str, str]] = {}
    for dep in manifest.get("dependencies", []):
        name = dep.get("name")
        if name:
            deps[name] = {
                "version": dep.get("version", ""),
                "hash": dep.get("hash", ""),
            }
    return deps


def _is_pinned(version: str) -> bool:
    """Heuristic: pinned versions look like '1.2.3' or 'sha256:...'."""
    if version.startswith("sha256:"):
        return True
    parts = version.split(".")
    return all(p.isdigit() for p in parts) if parts else False


def _detect_required_capabilities(asts: list[ast.Module]) -> set[str]:
    """Find capabilities the code needs based on AST patterns."""
    required: set[str] = set()
    for tree in asts:
        for node in ast.walk(tree):
            # File I/O
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "open"
            ):
                required.add("read:file")
                # If mode includes 'w'/'a' -> write, but we simplify
                required.add("write:file")
            # subprocess calls
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("run", "call", "Popen")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
            ):
                required.add("execute:subprocess")
            # Detect requests-based network calls
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr
                in ("get", "post", "put", "delete", "head", "options")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "requests"
            ):
                required.add("network:network")
            # generic socket / urllib
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                full_name = _dotted_name(node.func.value)
                if full_name in ("urllib.request", "socket"):
                    required.add("network:network")
    return required


def _dotted_name(node: ast.expr) -> str | None:
    """Resolve a dotted attribute chain like a.b.c to 'a.b.c'."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        if base is None:
            return None
        return f"{base}.{node.attr}"
    return None


def _normalize_capabilities(caps: dict[str, list[str]]) -> set[str]:
    """Turn the manifest capabilities block into a flat set of 'category:resource'."""
    normalized: set[str] = set()
    for category, resources in caps.items():
        for res in resources:
            normalized.add(f"{category}:{res}")
    return normalized
