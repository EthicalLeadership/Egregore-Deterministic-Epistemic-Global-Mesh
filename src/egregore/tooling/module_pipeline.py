"""Build-time CBI-0 module pipeline CLI.

Enforces plane/layer import boundaries (M1), manifest completeness (M2), and a
non-fatal cell-awareness stub (M5) for modules that use model/agent infrastructure.

This is a build-time gate, separate from the runtime CBI-0 chain in
``egregore.governance.cbi0_governance``. It emits an ``audit_report.json`` per
module without blocking runtime execution.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from egregore.shared.canonical import canonical_dumps
from egregore.tooling.pipeline_checkers import (
    AstImportAnalyzer,
    CapabilityScanner,
    M1Checker,
    M2Checker,
    M3Checker,
    M5CellAwarenessChecker,
    M5StubChecker,
    PlaneLayerClassifier,
)
from egregore.tooling.pipeline_graph import M2GraphBuilder
from egregore.tooling.pipeline_models import (
    AuditReport,
    CapabilityBlock,
    Cbi0Block,
    CheckResult,
    M3Block,
    ModuleManifest,
    Violation,
)
from egregore.tooling.pipeline_packager import (
    package_module,
    resolve_signing_key,
    write_zarc_bundle,
)
from egregore.tooling.pipeline_runner import ModulePipelineRunner

__all__ = [
    "AstImportAnalyzer",
    "AuditReport",
    "CapabilityBlock",
    "CapabilityScanner",
    "CheckResult",
    "Cbi0Block",
    "M1Checker",
    "M2Checker",
    "M3Checker",
    "M5CellAwarenessChecker",
    "M5StubChecker",
    "M3Block",
    "ModuleManifest",
    "ModulePipelineRunner",
    "PlaneLayerClassifier",
    "Violation",
    "main",
]

logger = logging.getLogger(__name__)


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def _cmd_check(args: argparse.Namespace) -> int:
    src_root = Path(args.src_root).resolve() if args.src_root else None
    runner = ModulePipelineRunner(pipeline_class=args.pipeline_class, src_root=src_root)
    module_dir = Path(args.module_dir).resolve()
    report = runner.run(module_dir)
    manifest = runner._load_manifest(module_dir)

    out_dir = Path(args.out_dir)
    manifest_path, report_path, zarc_path = package_module(
        out_dir=out_dir,
        module_id=report.module_id,
        manifest=manifest,
        report=report,
        signing_key_hex=args.signing_key_hex,
        timestamp_ns=report.timestamp_ns,
    )
    print(f"Wrote manifest: {manifest_path}")
    print(f"Wrote report:   {report_path}")
    print(f"Wrote bundle:   {zarc_path}")

    fail = report.m1["status"] == "FAIL" or (
        args.pipeline_class == "standard"
        and (report.m2["status"] == "FAIL" or report.m3["status"] == "FAIL")
    )
    return 1 if fail else 0


def _cmd_init_manifest(args: argparse.Namespace) -> int:
    runner = ModulePipelineRunner()
    manifest = runner.generate_manifest(Path(args.module_dir))
    output_path = Path(args.out)
    output_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    print(f"Wrote manifest to {output_path}")
    return 0


def _cmd_graph(args: argparse.Namespace) -> int:
    src_root = Path(args.src_root).resolve() if args.src_root else None
    builder = M2GraphBuilder(src_root=src_root)
    module_dirs = [Path(d) for d in args.modules]
    graph_report = builder.audit(module_dirs)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "m2_graph_report.json"
    report_path.write_text(
        canonical_dumps(graph_report.to_dict()),
        encoding="utf-8",
    )

    key = resolve_signing_key(args.signing_key_hex)
    zarc_path = out_dir / "graph.zarc"
    write_zarc_bundle(
        zarc_path=zarc_path,
        signing_key_hex=key,
        engine="module_pipeline",
        event="graph_audited",
        payload=graph_report.to_dict(),
        timestamp_ns=graph_report.timestamp_ns,
    )
    print(f"Wrote graph report: {report_path}")
    print(f"Wrote graph bundle: {zarc_path}")
    return 0 if graph_report.is_pass() else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Egregore build-time CBI-0 module pipeline"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser(
        "check", help="Run M1/M2/M5 checks on a module"
    )
    check_parser.add_argument(
        "--module-dir", required=True, help="Path to module directory or file"
    )
    check_parser.add_argument(
        "--class",
        dest="pipeline_class",
        choices=["fast", "standard"],
        default="standard",
        help="Pipeline class: fast runs M1 only; standard runs M1+M2+M5",
    )
    check_parser.add_argument(
        "--out-dir",
        default="pipeline_outputs",
        help="Output directory for manifest, report, and bundle.zarc",
    )
    check_parser.add_argument(
        "--src-root",
        default=None,
        help="Root of the source tree (default: repository src/)",
    )
    check_parser.add_argument(
        "--signing-key-hex",
        default=None,
        help="Ed25519 signing key hex (or set BLACKSTAR_SIGNING_KEY_HEX)",
    )
    check_parser.set_defaults(func=_cmd_check)

    init_parser = subparsers.add_parser(
        "init-manifest", help="Generate an initial egregore-module.json"
    )
    init_parser.add_argument(
        "--module-dir", required=True, help="Path to module directory or file"
    )
    init_parser.add_argument(
        "--out", required=True, help="Output egregore-module.json path"
    )
    init_parser.set_defaults(func=_cmd_init_manifest)

    graph_parser = subparsers.add_parser(
        "graph", help="Run M2 graph audit across modules"
    )
    graph_parser.add_argument(
        "--modules",
        nargs="+",
        required=True,
        help="One or more module directories",
    )
    graph_parser.add_argument(
        "--out-dir",
        default="pipeline_outputs",
        help="Output directory for m2_graph_report.json and graph.zarc",
    )
    graph_parser.add_argument(
        "--src-root",
        default=None,
        help="Root of the source tree (default: repository src/)",
    )
    graph_parser.add_argument(
        "--signing-key-hex",
        default=None,
        help="Ed25519 signing key hex (or set BLACKSTAR_SIGNING_KEY_HEX)",
    )
    graph_parser.set_defaults(func=_cmd_graph)

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
