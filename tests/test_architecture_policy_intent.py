from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
PKG_ROOT = SRC_ROOT / "egregore"

ALLOWED_CROSS_LAYER: dict[str, set[str]] = {
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
    "domain": {"interface", "shared"},
    "dt1": set(),
    "governance": {"infrastructure", "models", "shared"},
    "patterns": {"domain", "shared"},
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
    "services": {
        "application",
        "domain",
        "infrastructure",
        "interface",
        "kernel",
        "shared",
    },
    "shared": {"domain"},
    "tooling": {"application", "shared"},
}

ALLOWED_AUDIT_EVENT_FILES = {"egregore/domain/semantics/derivations.py"}
ALLOWED_OUTBOX_ENTRY_FILES = {"egregore/domain/semantics/derivations.py"}
ALLOWED_COMMIT_CALL_FILES = {"egregore/application/semantics_executor.py"}
ALLOWED_JSON_FILES = {
    "egregore/shared/canonical.py",
    "egregore/infrastructure/inter_node_messenger.py",
    "egregore/application/agent_registry.py",
    "egregore/application/agent_runner.py",
    "egregore/cli/admin.py",
    "egregore/infrastructure/local_llm_adapter.py",
    "egregore/infrastructure/local_model_client.py",
    "egregore/interface/dashboard/service.py",
    "egregore/infrastructure/persistence/user_repository.py",
    "egregore/dossiers/DOSS_01_sentinel_telemetry/mesh.py",
}
ALLOWED_TYPE_IGNORE_FILES = {
    "egregore/http_api/http/v1/dossiers.py",
    "metrics/cpu_ram.py",
    "metrics/gpu.py",
    "metrics/procbg.py",
}


def _iter_py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if p.is_file())


def _module_parts(path: Path) -> list[str]:
    rel = path.relative_to(SRC_ROOT).as_posix()
    parts = rel.split("/")
    if parts and parts[0] == "egregore":
        return parts
    return parts


def _ast_calls(tree: ast.AST) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def _is_constructor_call(call: ast.Call, name: str) -> bool:
    return isinstance(call.func, ast.Name) and call.func.id == name


def _is_attr_call(call: ast.Call, attr_name: str) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr == attr_name


def _layer_name(path: Path) -> str:
    rel = path.relative_to(PKG_ROOT)
    return rel.parts[0]


def _runtime_imports(tree: ast.AST) -> set[str]:  # noqa: C901
    """Return imported module names that are not guarded by ``if TYPE_CHECKING``."""
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def _under_type_checking(node: ast.AST) -> bool:
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

    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and _under_type_checking(
            node
        ):
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


@pytest.mark.parametrize("layer", sorted(ALLOWED_CROSS_LAYER))
def test_cross_layer_imports_are_explicitly_allowed(layer: str) -> None:
    allowed = ALLOWED_CROSS_LAYER[layer]
    violations: list[str] = []

    for path in _iter_py_files(PKG_ROOT / layer):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module in _runtime_imports(tree):
            if module.startswith("egregore."):
                dep = module.split(".")[1]
                if dep != layer and dep not in allowed:
                    violations.append(f"{path.relative_to(SRC_ROOT)} imports {module}")

    assert not violations, "Unexpected cross-layer imports:\n" + "\n".join(violations)


def test_audit_event_construction_is_single_sourced() -> None:
    offenders: list[str] = []

    for path in _iter_py_files(PKG_ROOT):
        if (
            path.relative_to(SRC_ROOT).as_posix()
            == "egregore/domain/semantics/derivations.py"
        ):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(_is_constructor_call(call, "AuditEvent") for call in _ast_calls(tree)):
            offenders.append(path.relative_to(SRC_ROOT).as_posix())

    assert (
        not offenders
    ), "AuditEvent must only be constructed in derivations.py:\n" + "\n".join(offenders)


def test_outbox_entry_construction_is_single_sourced() -> None:
    offenders: list[str] = []

    for path in _iter_py_files(PKG_ROOT):
        if (
            path.relative_to(SRC_ROOT).as_posix()
            == "egregore/domain/semantics/derivations.py"
        ):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(_is_constructor_call(call, "OutboxEntry") for call in _ast_calls(tree)):
            offenders.append(path.relative_to(SRC_ROOT).as_posix())

    assert (
        not offenders
    ), "OutboxEntry must only be constructed in derivations.py:\n" + "\n".join(
        offenders
    )


def test_commit_generate_t2_call_is_executor_only() -> None:
    offenders: list[str] = []

    for path in _iter_py_files(PKG_ROOT):
        rel = path.relative_to(SRC_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _ast_calls(tree):
            if (
                _is_attr_call(call, "commit_generate_t2")
                and rel not in ALLOWED_COMMIT_CALL_FILES
            ):
                offenders.append(rel)

    assert not offenders, (
        "commit_generate_t2 must only be called from semantics_executor.py:\n"
        + "\n".join(sorted(set(offenders)))
    )


def test_domain_remains_pure_of_application_and_infrastructure() -> None:
    violations: list[str] = []

    for path in _iter_py_files(PKG_ROOT / "domain"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(
                    "egregore.application"
                ) or node.module.startswith("egregore.infrastructure"):
                    violations.append(
                        f"{path.relative_to(SRC_ROOT)} imports {node.module}"
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if name.startswith("egregore.application") or name.startswith(
                        "egregore.infrastructure"
                    ):
                        violations.append(
                            f"{path.relative_to(SRC_ROOT)} imports {name}"
                        )

    assert (
        not violations
    ), "Domain must not import application/infrastructure:\n" + "\n".join(violations)


def test_json_loads_and_dumps_have_one_home() -> None:
    violations: list[str] = []

    for path in _iter_py_files(PKG_ROOT):
        rel = path.relative_to(SRC_ROOT).as_posix()
        if rel in ALLOWED_JSON_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        if "json.loads(" in text or "json.dumps(" in text:
            violations.append(rel)

    assert (
        not violations
    ), "json.loads/json.dumps must only live in shared/canonical.py:\n" + "\n".join(
        sorted(set(violations))
    )


def test_type_ignores_have_known_justification() -> None:
    violations: list[str] = []

    for path in _iter_py_files(PKG_ROOT):
        rel = path.relative_to(SRC_ROOT).as_posix()
        if rel.startswith("egregore/http_api/http/"):
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "type: ignore" not in line:
                continue
            if rel in ALLOWED_TYPE_IGNORE_FILES:
                continue
            context = "\n".join(
                text.splitlines()[max(0, lineno - 2) : lineno + 1]
            ).lower()
            if not any(
                token in context
                for token in (
                    "optional dependency",
                    "pragma: no cover",
                    "compatibility",
                    "adr",
                    "justification",
                )
            ):
                violations.append(f"{rel}:{lineno}")

    assert (
        not violations
    ), "type: ignore needs a visible justification or allowlist:\n" + "\n".join(
        violations
    )
