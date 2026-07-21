import ast
import re
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
PKG_ROOT = SRC_ROOT / "egregore"


def iter_py_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.py") if p.is_file())


FORBIDDEN_IMPORT_PREFIXES = {
    "domain": ["egregore.infrastructure", "egregore.application"],
    "application": [
        "egregore.infrastructure"
    ],  # application must call ports/abstractions
}

# Extra “purity” constraints for domain.
FORBIDDEN_DOMAIN_IMPORT_MODULES = {
    "pathlib",
    "os",
    "sys",
    "subprocess",
    "socket",
    "requests",
    "urllib",
    "http",
    "multiprocessing",
}
FORBIDDEN_DOMAIN_CALL_NAMES = {"open"}

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
    "dossiers": set(),  # Self-contained dossier packages, no upstream imports
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

ALLOWED_ENGINE_IMPORTERS = {
    "egregore/application/cbi_0_orchestrated_executor.py",
}

ALLOWED_EXECUTION_AUTHORITY_USERS = {
    "egregore/application/cbi_0_orchestrated_executor.py",
    "egregore/application/legal_reasoning_engine.py",
    "egregore/domain/legal_agent/execution_authority.py",
}

ALLOWED_KERNEL_DOMAIN_IMPORTS = {
    "egregore/kernel/scheduler/dt_monitor.py",
    "egregore/kernel/scheduler/epoch_scheduler.py",
    "egregore/kernel/scheduler/powertrain_coupling.py",
    "egregore/kernel/scheduler/tu_budget.py",
}

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

# Pre-existing interface→governance import (ombudsman router).
ALLOWED_INTERFACE_GOVERNANCE_IMPORTS = {
    "egregore/interface/ombudsman_router.py",
}


def _under_type_checking(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
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


def ast_imports(module: ast.AST) -> set[str]:
    imports: set[str] = set()
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(module):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    for node in ast.walk(module):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and _under_type_checking(
            node, parents
        ):
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


def has_forbidden_domain_calls(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # open(...)
            if (
                isinstance(node.func, ast.Name)
                and node.func.id in FORBIDDEN_DOMAIN_CALL_NAMES
            ):
                return True
            # Path(...).open(...) patterns aren’t common in our domain, but keep it strict.
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in FORBIDDEN_DOMAIN_CALL_NAMES
            ):
                return True
    return False


def find_function_defs(text: str, fn_name: str) -> list[tuple[int, int]]:
    # returns list of (start_index, end_index) for each def match roughly
    # avoid heavy parsing by using regex.
    pattern = rf"(?m)^def\s+{re.escape(fn_name)}\s*\("
    return list(re.finditer(pattern, text))


@pytest.mark.parametrize(
    "layer,rel_root",
    [
        ("domain", PKG_ROOT / "domain"),
        ("application", PKG_ROOT / "application"),
    ],
)
def test_dependency_rules_enforced(layer: str, rel_root: Path) -> None:
    forbidden_prefixes = FORBIDDEN_IMPORT_PREFIXES[layer]
    files = iter_py_files(rel_root)
    assert files, f"No python files found under {rel_root}"

    violations: list[str] = []
    for f in files:
        rel = f.relative_to(SRC_ROOT).as_posix()
        tree = ast.parse(f.read_text(encoding="utf-8"))
        imports = ast_imports(tree)
        for imp in sorted(imports):
            for pref in forbidden_prefixes:
                if imp == pref or imp.startswith(pref + "."):
                    if (
                        layer == "application"
                        and pref == "egregore.infrastructure"
                        and rel in ALLOWED_APPLICATION_INFRASTRUCTURE_IMPORTS
                    ):
                        break
                    violations.append(
                        f"{f.relative_to(SRC_ROOT)} imports forbidden '{imp}'"
                    )
                    break

    assert not violations, "Forbidden dependency violations:\n" + "\n".join(violations)


def test_domain_purity_no_filesystem_network_heuristics() -> None:
    files = iter_py_files(PKG_ROOT / "domain")
    assert files, "No domain python files found"

    violations: list[str] = []

    for f in files:
        text = f.read_text(encoding="utf-8")
        tree = ast.parse(text)
        imports = ast_imports(tree)

        for imp in sorted(imports):
            # import pathlib, from pathlib import Path
            for mod in FORBIDDEN_DOMAIN_IMPORT_MODULES:
                if imp == mod or imp.startswith(mod + "."):
                    violations.append(
                        f"{f.relative_to(SRC_ROOT)} imports forbidden module '{imp}'"
                    )
        if has_forbidden_domain_calls(tree):
            violations.append(
                f"{f.relative_to(SRC_ROOT)} calls forbidden function (open)"
            )

    assert not violations, "Domain purity violations:\n" + "\n".join(violations)


def test_canonicalization_single_source_of_truth() -> None:
    """
    Enforce that canonical_json/sha256_hex are only defined in shared/canonical.py.
    This avoids signature/hash correctness drift.
    """
    canonical_path = PKG_ROOT / "shared" / "canonical.py"
    canonical_text = canonical_path.read_text(encoding="utf-8")
    assert "def canonical_json" in canonical_text
    assert "def sha256_hex" in canonical_text

    offenders: list[str] = []
    for f in iter_py_files(PKG_ROOT):
        if f == canonical_path:
            continue
        text = f.read_text(encoding="utf-8")
        if "def canonical_json" in text or "def sha256_hex" in text:
            # be conservative: if either function is defined, flag it.
            if find_function_defs(text, "canonical_json") or find_function_defs(
                text, "sha256_hex"
            ):
                offenders.append(f"{f.relative_to(SRC_ROOT)}")

    assert not offenders, (
        "canonical_json/sha256_hex must be defined only in shared/canonical.py:\n"
        + "\n".join(offenders)
    )


def test_legal_reasoning_engine_import_surface_is_closed() -> None:
    """
    Adversarial guardrail: only the CBI orchestrator may import LegalReasoningEngine
    in source code. This prevents silent direct execution path expansion.
    """
    offenders: list[str] = []

    for f in iter_py_files(PKG_ROOT):
        rel = f.relative_to(SRC_ROOT).as_posix()
        text = f.read_text(encoding="utf-8")
        if (
            "from egregore.application.legal_reasoning_engine import LegalReasoningEngine"
            in text
        ):
            if rel not in ALLOWED_ENGINE_IMPORTERS:
                offenders.append(rel)

    assert not offenders, (
        "Unauthorized LegalReasoningEngine imports (bypass expansion risk):\n"
        + "\n".join(offenders)
    )


def test_execution_authority_usage_surface_is_closed() -> None:
    """
    Adversarial guardrail: governed scope controls must remain restricted to the
    legal engine, orchestrator, and authority module itself.
    """
    offenders: list[str] = []

    for f in iter_py_files(PKG_ROOT):
        rel = f.relative_to(SRC_ROOT).as_posix()
        text = f.read_text(encoding="utf-8")

        if (
            "ExecutionAuthority" in text
            and rel not in ALLOWED_EXECUTION_AUTHORITY_USERS
        ):
            offenders.append(rel)

        if (
            "ExecutionAuthority.governed(" in text
            and rel != "egregore/application/cbi_0_orchestrated_executor.py"
        ):
            offenders.append(rel)

    assert (
        not offenders
    ), "Unauthorized ExecutionAuthority usage (governance bypass risk):\n" + "\n".join(
        sorted(set(offenders))
    )


def test_layer_dependency_matrix_is_stable() -> None:
    """
    Adversarial drift guard: all top-level package layers must keep imports
    within the approved cross-layer dependency matrix.
    """
    layers = sorted(
        p.name for p in PKG_ROOT.iterdir() if p.is_dir() and not p.name.startswith("__")
    )
    missing = sorted(set(layers) - set(ALLOWED_LAYER_DEPENDENCIES))
    extra = sorted(set(ALLOWED_LAYER_DEPENDENCIES) - set(layers))
    assert not missing, f"ALLOWED_LAYER_DEPENDENCIES missing layers: {missing}"
    assert not extra, f"ALLOWED_LAYER_DEPENDENCIES has unknown layers: {extra}"

    violations: list[str] = []

    for layer in layers:
        allowed = ALLOWED_LAYER_DEPENDENCIES[layer]
        for f in iter_py_files(PKG_ROOT / layer):
            rel = f.relative_to(SRC_ROOT).as_posix()
            tree = ast.parse(f.read_text(encoding="utf-8"))
            imports = ast_imports(tree)
            for imp in sorted(imports):
                if not imp.startswith("egregore."):
                    continue
                parts = imp.split(".")
                if len(parts) < 2:
                    continue
                dep = parts[1]
                if dep == layer:
                    continue
                if dep not in allowed:
                    # Whitelist: kernel scheduler files may import domain
                    if (
                        layer == "kernel"
                        and dep == "domain"
                        and rel in ALLOWED_KERNEL_DOMAIN_IMPORTS
                    ):
                        continue
                    # Whitelist: application files may import infrastructure
                    if (
                        layer == "application"
                        and dep == "infrastructure"
                        and rel in ALLOWED_APPLICATION_INFRASTRUCTURE_IMPORTS
                    ):
                        continue
                    # Whitelist: pre-existing interface→governance import
                    if (
                        layer == "interface"
                        and dep == "governance"
                        and rel in ALLOWED_INTERFACE_GOVERNANCE_IMPORTS
                    ):
                        continue
                    violations.append(
                        f"{rel} imports egregore.{dep} (layer={layer}, allowed={sorted(allowed)})"
                    )

    assert not violations, "Unexpected cross-layer dependency drift:\n" + "\n".join(
        violations
    )
