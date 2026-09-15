"""Import-boundary enforcement for shared kernel modules."""

from __future__ import annotations

import ast
from pathlib import Path


FORBIDDEN_ROOT_IMPORTS = frozenset(
    {
        "tk",
        "tkinter",
        "ui_text",
        "anchorum_desktop",
        "site",
    }
)


def _kernel_modules(root: Path) -> list[Path]:
    modules = sorted(root.glob("*_kernel.py"))
    modules.extend(sorted(root.glob("src/**/kernel/**/*.py")))
    return sorted(set(modules))


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
        elif isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
    return imports


def test_shared_kernel_modules_have_no_ui_or_site_imports() -> None:
    root = Path(__file__).resolve().parents[1]
    violations = {
        str(path.relative_to(root)): sorted(_import_roots(path) & FORBIDDEN_ROOT_IMPORTS)
        for path in _kernel_modules(root)
        if _import_roots(path) & FORBIDDEN_ROOT_IMPORTS
    }
    assert violations == {}
