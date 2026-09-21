"""Single-module pipeline runner."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from egregore.shared.canonical import canonical_loads
from egregore.tooling.pipeline_checkers import (
    AstImportAnalyzer,
    CapabilityScanner,
    M1Checker,
    M2Checker,
    M3Checker,
    M5StubChecker,
    PlaneLayerClassifier,
)
from egregore.tooling.pipeline_models import (
    AuditReport,
    CapabilityBlock,
    Cbi0Block,
    M3Block,
    ModuleManifest,
    _load_project_version,
)


class ModulePipelineRunner:
    def __init__(
        self,
        pipeline_class: str = "standard",
        src_root: Path | None = None,
    ) -> None:
        self.pipeline_class = pipeline_class
        self.src_root = src_root or Path(__file__).resolve().parents[2]
        self.classifier = PlaneLayerClassifier()
        self.capability_scanner = CapabilityScanner()
        self.m1_checker = M1Checker(self.classifier, self._load_plane1_ports())
        self.m2_checker = M2Checker(_load_project_version())
        self.m3_checker = M3Checker()
        self.m5_checker = M5StubChecker()

    def _load_plane1_ports(self) -> dict[str, Any]:
        data_path = Path(__file__).with_suffix("").parent / "data" / "plane1_ports.json"
        if data_path.exists():
            return canonical_loads(data_path.read_text(encoding="utf-8"))
        return {}

    def run(self, module_dir: Path) -> AuditReport:
        module_dir = module_dir.resolve()
        if module_dir.is_file():
            module_dir = module_dir.parent

        rel_dir = module_dir.relative_to(self.src_root)
        module_name = ".".join(rel_dir.with_suffix("").parts)
        manifest = self._load_manifest(module_dir)

        all_imports: set[str] = set()
        all_full_imports: set[str] = set()
        all_capabilities: dict[str, list[str]] = {
            name: [] for name in ["read", "write", "execute", "network"]
        }
        sources: list[tuple[Path, str]] = []
        file_imports: dict[Path, set[str]] = {}
        for py_file in sorted(module_dir.rglob("*.py")):
            if py_file.name.startswith("test_"):
                continue
            source = py_file.read_text(encoding="utf-8")
            sources.append((py_file, source))
            analyzer = AstImportAnalyzer(source, module_name)
            file_egregore_imports = analyzer.egregore_imports()
            file_imports[py_file] = file_egregore_imports
            all_imports.update(file_egregore_imports)
            all_full_imports.update(
                imp for imp in analyzer.imports() if imp.startswith("egregore.")
            )
            caps = self.capability_scanner.scan(source)
            for cap_name, usages in caps.items():
                all_capabilities[cap_name].extend(usages)

        layer = manifest.cbi0.m1_layer

        m1_result = self.m1_checker.check(
            module_name,
            layer,
            all_imports,
            file_imports=file_imports,
            src_root=self.src_root,
        )
        m2_result = self.m2_checker.check(
            module_name, layer, all_imports, all_capabilities, manifest
        )
        m3_result = self.m3_checker.check(sources, manifest)
        m5_result = self.m5_checker.check(all_full_imports, sources, manifest)

        if self.pipeline_class == "fast":
            m2_result.status = "NOT_VERIFIED"
            m2_result.metadata = {"note": "Fast pipeline class skips M2."}
            m3_result.status = "NOT_VERIFIED"
            m3_result.metadata = {"note": "Fast pipeline class skips M3."}
            m3_result.violations = []
            m5_result.status = "NOT_ENFORCED"
            m5_result.metadata = {"note": "Fast pipeline class skips M5."}

        return AuditReport(
            module_id=manifest.module_id,
            timestamp_ns=int(time.time_ns()),
            pipeline_class=self.pipeline_class,
            m1=m1_result.to_dict(),
            m2=m2_result.to_dict(),
            m3=m3_result.to_dict(),
            m4={
                "status": "DIVERGED",
                "note": "No spec file provided; equivalence not checked",
            },
            m5=m5_result.to_dict(),
        )

    def _load_manifest(self, module_dir: Path) -> ModuleManifest:
        module_dir = module_dir.resolve()
        manifest_path = module_dir / "egregore-module.json"
        if manifest_path.exists():
            return ModuleManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        rel_dir = module_dir.relative_to(self.src_root)
        module_name = ".".join(rel_dir.with_suffix("").parts)
        plane, layer = self.classifier.classify(module_name)
        return ModuleManifest(
            module_id=module_name,
            version=_load_project_version(),
            cbi0=Cbi0Block(m1_plane=plane, m1_layer=layer, m3=M3Block(terminal=False)),
        )

    def generate_manifest(self, module_dir: Path) -> ModuleManifest:
        module_dir = module_dir.resolve()
        if module_dir.is_file():
            module_dir = module_dir.parent

        rel_dir = module_dir.relative_to(self.src_root)
        module_name = ".".join(rel_dir.with_suffix("").parts)
        plane, layer = self.classifier.classify(module_name)

        all_imports: set[str] = set()
        all_capabilities: dict[str, list[str]] = {
            name: [] for name in ["read", "write", "execute", "network"]
        }
        for py_file in sorted(module_dir.rglob("*.py")):
            if py_file.name.startswith("test_"):
                continue
            source = py_file.read_text(encoding="utf-8")
            analyzer = AstImportAnalyzer(source, module_name)
            all_imports.update(analyzer.egregore_imports())
            caps = self.capability_scanner.scan(source)
            for cap_name, usages in caps.items():
                all_capabilities[cap_name].extend(usages)

        deps: list[dict[str, str]] = []
        for imp in sorted(all_imports):
            if imp == module_name:
                continue
            h = self.m2_checker._hash_source(imp)
            deps.append(
                {
                    "module": imp,
                    "version": _load_project_version(),
                    "hash": f"sha256:{h}" if h else "",
                }
            )

        uses_model_agent = any(
            imp == prefix or imp.startswith(prefix + ".")
            for imp in all_imports
            for prefix in [
                "egregore.infrastructure.local_model_catalog",
                "egregore.infrastructure.gguf_catalog",
                "egregore.infrastructure.local_model_client",
                "egregore.application.agent_registry",
                "egregore.application.agent_runner",
                "egregore.application.chat_interpreter",
            ]
        )

        capability_block = CapabilityBlock(
            read=all_capabilities.get("read", []),
            write=all_capabilities.get("write", []),
            execute=all_capabilities.get("execute", []),
            network=all_capabilities.get("network", []),
        )

        return ModuleManifest(
            module_id=module_name,
            version=_load_project_version(),
            cbi0=Cbi0Block(
                m1_plane=plane,
                m1_layer=layer,
                m2_dependencies=deps,
                m2_capabilities=capability_block,
                m3=M3Block(terminal=False),
                m5_cell_aware=uses_model_agent,
            ),
        )
