"""CLI for the Egregore integration-pipeline governance triad."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from egregore.pipeline import IntegrationPipeline, generate_signing_key
from egregore.shared.canonical import canonical_dumps, canonical_loads


def _load_json_list(path: Path) -> list[str]:
    data = canonical_loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON list")
    return [str(item) for item in data]


def _private_key_from_hex(key_hex: str | None) -> Ed25519PrivateKey | None:
    if not key_hex:
        return None
    key_bytes = bytes.fromhex(key_hex)
    return Ed25519PrivateKey.from_private_bytes(key_bytes)


def _cmd_check(args: argparse.Namespace) -> int:
    module_dir = Path(args.module_dir).resolve()
    config_dir = Path(args.config_dir).resolve() if args.config_dir else module_dir

    plane1_ports = _load_json_list(config_dir / "plane1_ports.json")
    concrete_infrastructure = _load_json_list(
        config_dir / "concrete_infrastructure.json"
    )
    port_registry = _load_json_list(config_dir / "port_registry.json")

    private_key = _private_key_from_hex(args.signing_key_hex)

    pipeline = IntegrationPipeline(
        plane1_ports=plane1_ports,
        concrete_infrastructure=concrete_infrastructure,
        port_registry=port_registry,
        signer_id=args.signer_id,
        private_key=private_key,
    )
    report = pipeline.run(module_dir)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "integration_report.json"
    report_path.write_text(
        canonical_dumps(report.to_dict(), indent=2),
        encoding="utf-8",
    )
    print(f"Wrote integration report: {report_path}")

    if report.provenance:
        provenance_path = out_dir / "provenance.json"
        provenance_path.write_text(
            canonical_dumps(report.provenance, indent=2),
            encoding="utf-8",
        )
        print(f"Wrote provenance:         {provenance_path}")

    return 0 if report.is_pass() else 1


def _cmd_generate_key(args: argparse.Namespace) -> int:
    private_pem, public_pem = generate_signing_key(args.out_dir)
    print(f"Wrote signing key pair to {args.out_dir}")
    print("Private key fingerprint (SHA-256):")
    print(f"  {private_pem.strip().splitlines()[-1][:32]}...")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Egregore integration-pipeline governance triad"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser(
        "check", help="Validate manifest and run M1/M2 checks"
    )
    check_parser.add_argument(
        "--module-dir", required=True, help="Path to module directory"
    )
    check_parser.add_argument(
        "--config-dir",
        default=None,
        help="Directory containing plane1_ports.json, concrete_infrastructure.json, "
        "and port_registry.json (default: module-dir)",
    )
    check_parser.add_argument(
        "--out-dir",
        default="pipeline_outputs",
        help="Output directory for integration_report.json and provenance.json",
    )
    check_parser.add_argument(
        "--signing-key-hex",
        default=None,
        help="Ed25519 signing key hex (or set EGREGORE_SIGNING_KEY_HEX)",
    )
    check_parser.add_argument(
        "--signer-id",
        default="egregore-pipeline",
        help="Identifier recorded in the provenance block",
    )
    check_parser.set_defaults(func=_cmd_check)

    key_parser = subparsers.add_parser(
        "generate-key", help="Generate an Ed25519 key pair for provenance signing"
    )
    key_parser.add_argument(
        "--out-dir",
        default=".",
        help="Directory to write signing_key.pem and signing_key.pub",
    )
    key_parser.set_defaults(func=_cmd_generate_key)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
