"""Tests for the M2 multi-module dependency graph audit."""

from __future__ import annotations

from pathlib import Path

from egregore.tooling.pipeline_graph import M2GraphBuilder
from egregore.tooling.pipeline_models import Cbi0Block, ModuleManifest


def _write_module(
    root: Path, module_id: str, source: str, deps: list[dict[str, str]] | None = None
) -> Path:
    parts = module_id.split(".")
    module_dir = root.joinpath(*parts)
    module_dir.mkdir(parents=True)
    (module_dir / "__init__.py").write_text(source, encoding="utf-8")
    manifest = ModuleManifest(
        module_id=module_id,
        version="0.1.0",
        cbi0=Cbi0Block(
            m1_plane="shared",
            m1_layer="shared",
            m2_dependencies=deps or [],
        ),
    )
    (module_dir / "egregore-module.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return module_dir


def test_graph_passes_with_consistent_dependencies(tmp_path: Path) -> None:
    root = tmp_path / "src"
    a = _write_module(root, "egregore.test_a", "x = 1")
    b = _write_module(
        root,
        "egregore.test_b",
        "import egregore.test_a",
        [{"module": "egregore.test_a", "version": "0.1.0", "hash": "sha256:abc"}],
    )

    builder = M2GraphBuilder(src_root=root)
    report = builder.audit([a, b])

    assert report.is_pass()
    assert not report.undeclared_edges
    assert not report.version_mismatches
    assert not report.hash_mismatches
    assert not report.cycles


def test_graph_detects_undeclared_edge(tmp_path: Path) -> None:
    root = tmp_path / "src"
    a = _write_module(root, "egregore.test_a", "x = 1")
    b = _write_module(root, "egregore.test_b", "import egregore.test_a", [])

    builder = M2GraphBuilder(src_root=root)
    report = builder.audit([a, b])

    assert not report.is_pass()
    assert any(
        e["source"] == "egregore.test_b" and e["target"] == "egregore.test_a"
        for e in report.undeclared_edges
    )


def test_graph_detects_version_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "src"
    a = _write_module(root, "egregore.test_a", "x = 1")
    b = _write_module(
        root,
        "egregore.test_b",
        "import egregore.test_a",
        [{"module": "egregore.test_a", "version": "0.1.0", "hash": "sha256:abc"}],
    )
    c = _write_module(
        root,
        "egregore.test_c",
        "import egregore.test_a",
        [{"module": "egregore.test_a", "version": "0.2.0", "hash": "sha256:abc"}],
    )

    builder = M2GraphBuilder(src_root=root)
    report = builder.audit([a, b, c])

    assert not report.is_pass()
    assert report.version_mismatches


def test_graph_detects_cycle(tmp_path: Path) -> None:
    root = tmp_path / "src"
    a = _write_module(
        root,
        "egregore.test_a",
        "x = 1",
        [{"module": "egregore.test_b", "version": "0.1.0", "hash": ""}],
    )
    b = _write_module(
        root,
        "egregore.test_b",
        "x = 1",
        [{"module": "egregore.test_a", "version": "0.1.0", "hash": ""}],
    )

    builder = M2GraphBuilder(src_root=root)
    report = builder.audit([a, b])

    assert not report.is_pass()
    assert report.cycles
