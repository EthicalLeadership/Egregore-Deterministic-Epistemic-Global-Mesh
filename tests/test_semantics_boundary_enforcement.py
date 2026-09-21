from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
APP_ROOT = SRC_ROOT / "egregore" / "application"


def iter_py_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.py") if p.is_file())


FORBIDDEN_CONSTRUCT_NAMES = {
    # Semantic artifacts must be derived in domain only.
    "AuditEvent",
    "OutboxEntry",
    "DossierSnapshot",
}


def ast_constructs_any(tree: ast.AST, *, names: set[str]) -> set[str]:
    """
    Returns the subset of `names` that appear as constructor calls in the AST.

    Match patterns:
    - AuditEvent(...)
    - OutboxEntry(...)
    - DossierSnapshot(...)
    """
    found: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # plain AuditEvent call
            if isinstance(node.func, ast.Name) and node.func.id in names:
                found.add(node.func.id)
            # module.AuditEvent(...) is currently not supported/forbidden by convention,
            # but keep detection strict for now.
            if isinstance(node.func, ast.Attribute) and node.func.attr in names:
                found.add(node.func.attr)

    return found


@pytest.mark.parametrize("f", iter_py_files(APP_ROOT))
def test_application_layer_must_not_construct_semantic_artifacts(f: Path) -> None:
    tree = ast.parse(f.read_text(encoding="utf-8"))
    found = ast_constructs_any(tree, names=FORBIDDEN_CONSTRUCT_NAMES)
    assert not found, f"{f.relative_to(SRC_ROOT)} illegally constructs: {sorted(found)}"
