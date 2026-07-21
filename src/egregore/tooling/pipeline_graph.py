"""Multi-module M2 dependency graph audit."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from egregore.tooling.pipeline_checkers import AstImportAnalyzer
from egregore.tooling.pipeline_models import ModuleManifest
from egregore.tooling.pipeline_runner import ModulePipelineRunner


class M2GraphReport:
    def __init__(self, timestamp_ns: int) -> None:
        self.timestamp_ns = timestamp_ns
        self.m1_failures: list[dict[str, Any]] = []
        self.undeclared_edges: list[dict[str, str]] = []
        self.version_mismatches: list[dict[str, Any]] = []
        self.hash_mismatches: list[dict[str, Any]] = []
        self.cycles: list[list[str]] = []
        self.graph: dict[str, dict[str, Any]] = {}

    def is_pass(self) -> bool:
        return not any(
            [
                self.m1_failures,
                self.undeclared_edges,
                self.version_mismatches,
                self.hash_mismatches,
                self.cycles,
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_ns": self.timestamp_ns,
            "status": "PASS" if self.is_pass() else "FAIL",
            "m1_failures": self.m1_failures,
            "undeclared_edges": self.undeclared_edges,
            "version_mismatches": self.version_mismatches,
            "hash_mismatches": self.hash_mismatches,
            "cycles": self.cycles,
            "graph": self.graph,
        }


class M2GraphBuilder:
    def __init__(self, src_root: Path | None = None) -> None:
        self.runner = ModulePipelineRunner(pipeline_class="fast", src_root=src_root)

    def audit(self, module_dirs: list[Path]) -> M2GraphReport:
        import time

        report = M2GraphReport(timestamp_ns=int(time.time_ns()))
        manifests: dict[str, ModuleManifest] = {}
        imports_by_module: dict[str, set[str]] = {}

        for module_dir in module_dirs:
            module_dir = module_dir.resolve()
            audit_report = self.runner.run(module_dir)
            if audit_report.m1["status"] == "FAIL":
                report.m1_failures.append(
                    {
                        "module_id": audit_report.module_id,
                        "violations": audit_report.m1["violations"],
                    }
                )
                continue
            manifest = self.runner._load_manifest(module_dir)
            manifests[manifest.module_id] = manifest
            imports_by_module[manifest.module_id] = self._collect_imports(
                module_dir, manifest.module_id
            )

        if report.m1_failures:
            return report

        self._build_graph(report, manifests)
        self._check_undeclared_edges(report, manifests, imports_by_module)
        self._check_version_and_hash_consistency(report, manifests)
        self._check_cycles(report, manifests)
        return report

    def _collect_imports(self, module_dir: Path, module_name: str) -> set[str]:
        imports: set[str] = set()
        for py_file in sorted(module_dir.rglob("*.py")):
            if py_file.name.startswith("test_"):
                continue
            source = py_file.read_text(encoding="utf-8")
            analyzer = AstImportAnalyzer(source, module_name)
            imports.update(analyzer.egregore_imports())
        return imports

    def _build_graph(
        self, report: M2GraphReport, manifests: dict[str, ModuleManifest]
    ) -> None:
        for module_id, manifest in manifests.items():
            report.graph[module_id] = {
                "version": manifest.version,
                "plane": manifest.cbi0.m1_plane,
                "layer": manifest.cbi0.m1_layer,
                "dependencies": [
                    {
                        "module": dep.get("module"),
                        "version": dep.get("version"),
                        "hash": dep.get("hash"),
                    }
                    for dep in manifest.cbi0.m2_dependencies
                ],
            }

    def _check_undeclared_edges(
        self,
        report: M2GraphReport,
        manifests: dict[str, ModuleManifest],
        imports_by_module: dict[str, set[str]],
    ) -> None:
        for module_id, manifest in manifests.items():
            declared = {d.get("module") for d in manifest.cbi0.m2_dependencies}
            for imp in imports_by_module.get(module_id, set()):
                if imp == module_id:
                    continue
                if imp not in declared:
                    report.undeclared_edges.append(
                        {"source": module_id, "target": imp, "rule": "M2-DEP"}
                    )

    def _check_version_and_hash_consistency(
        self,
        report: M2GraphReport,
        manifests: dict[str, ModuleManifest],
    ) -> None:
        by_target: dict[str, list[tuple[str, str, str]]] = {}
        for module_id, manifest in manifests.items():
            for dep in manifest.cbi0.m2_dependencies:
                target = dep.get("module")
                version = dep.get("version", "")
                hash_value = dep.get("hash", "")
                if target:
                    by_target.setdefault(target, []).append(
                        (module_id, version, hash_value)
                    )

        for target, refs in by_target.items():
            versions = {v for _, v, _ in refs}
            if len(versions) > 1:
                report.version_mismatches.append(
                    {
                        "module": target,
                        "versions": sorted(versions),
                        "consumers": [src for src, _, _ in refs],
                    }
                )
            for version in versions:
                hashes = {h for _, v, h in refs if v == version and h}
                if len(hashes) > 1:
                    report.hash_mismatches.append(
                        {
                            "module": target,
                            "version": version,
                            "hashes": sorted(hashes),
                            "consumers": [src for src, v, h in refs if v == version],
                        }
                    )

    def _check_cycles(
        self, report: M2GraphReport, manifests: dict[str, ModuleManifest]
    ) -> None:
        graph = {
            module_id: [
                d.get("module")
                for d in manifest.cbi0.m2_dependencies
                if d.get("module")
            ]
            for module_id, manifest in manifests.items()
        }

        visited: set[str] = set()
        rec_stack: set[str] = set()
        path: list[str] = []

        def dfs(node: str) -> None:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)
            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in rec_stack:
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:] + [neighbor]
                    if cycle not in report.cycles:
                        report.cycles.append(cycle)
            path.pop()
            rec_stack.remove(node)

        for node in sorted(graph):
            if node not in visited:
                dfs(node)
