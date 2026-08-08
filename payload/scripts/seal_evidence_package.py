#!/usr/bin/env python3
"""Seal evidence into a BagIt-profile package with a signed report.

Examples:
    python scripts/seal_evidence_package.py \
        --chain main=~/egregore_data/pioneer1/blocks.zarc \
        --blocks ~/egregore_data/pioneer1/blocks.zarc \
        --zarc ~/egregore_data/pioneer1/custody.zarc \
        --subject case_id=MOLSON-2026 --subject evidence_id=EV-001 \
        --evidence-id EV-001 \
        --out packages/MOLSON-2026-sealed --zip

Signing backend is selected via the standard env
(EGREGORE_SIGNING_BACKEND=local|pkcs11; see
infrastructure/pkcs11_signing_backend.py).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from egregore.application.evidence_sealer import EvidenceSealer
from egregore.infrastructure.pkcs11_signing_backend import (
    build_signing_backend_from_env,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chain",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help=".zarc chain to include (repeatable)",
    )
    parser.add_argument("--blocks", default=None, help="blocks.zarc path")
    parser.add_argument(
        "--anchors-db", default=None, help="SQLite anchor store path"
    )
    parser.add_argument(
        "--zarc",
        default=None,
        help="live .zarc for custody/witness export (requires --signing-key)",
    )
    parser.add_argument(
        "--signing-key",
        default=None,
        help="hex signing key for reading the live --zarc chain",
    )
    parser.add_argument(
        "--subject",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="subject index entry (repeatable)",
    )
    parser.add_argument("--evidence-id", default=None)
    parser.add_argument("--custody-actor", default="evidence-sealer")
    parser.add_argument("--custody-role", default="system")
    parser.add_argument("--tsa-trust-dir", default=None)
    parser.add_argument("--min-witnesses", type=int, default=0)
    parser.add_argument("--timestamp-ns", type=int, default=None)
    parser.add_argument("--out", required=True, help="output package directory")
    parser.add_argument("--zip", action="store_true", help="also write <out>.zip")
    args = parser.parse_args()

    chains: dict[str, Path] = {}
    for spec in args.chain:
        if "=" not in spec:
            parser.error(f"--chain expects NAME=PATH, got {spec!r}")
        name, path = spec.split("=", 1)
        chains[name] = Path(path).expanduser()
    if not chains and not args.zarc:
        parser.error("provide at least one --chain (or --zarc)")
    if args.zarc:
        chains.setdefault("main", Path(args.zarc).expanduser())

    subject = dict(
        spec.split("=", 1) for spec in args.subject if "=" in spec
    )

    timestamp_ns = args.timestamp_ns
    if timestamp_ns is None:
        import time

        timestamp_ns = time.time_ns()

    backend = build_signing_backend_from_env()
    if backend is None:
        print(
            "FAIL: no signing backend configured "
            "(EGREGORE_SIGNING_KEY_HEX or EGREGORE_SIGNING_BACKEND=pkcs11)"
        )
        return 2

    anchors = None
    if args.anchors_db:
        from dataclasses import asdict

        from egregore.infrastructure.persistence.sqlite_anchor_store import (
            SQLiteAnchorStore,
        )

        anchors = [
            asdict(record)
            for record in SQLiteAnchorStore(
                str(Path(args.anchors_db).expanduser())
            ).list_all()
        ]

    provenance = None
    if args.zarc and args.signing_key:
        from egregore.kernel.provenance import Provenance

        provenance = Provenance(
            Path(args.zarc).expanduser(), signing_key_hex=args.signing_key
        )

    sealer = EvidenceSealer(signing_backend=backend)
    from egregore.infrastructure.tsa_verifier import verify_tsa_token

    receipt = sealer.seal(
        Path(args.out),
        chains=chains,
        subject=subject,
        timestamp_ns=timestamp_ns,
        block_store_path=Path(args.blocks).expanduser() if args.blocks else None,
        anchors=anchors,
        provenance=provenance,
        tsa_trust_dir=Path(args.tsa_trust_dir) if args.tsa_trust_dir else None,
        min_witnesses=args.min_witnesses,
        custody_evidence_id=args.evidence_id,
        custody_actor=args.custody_actor,
        custody_role=args.custody_role,
        make_zip=args.zip,
        tsa_verify=verify_tsa_token,
    )
    print(f"seal_id:  {receipt.seal_id}")
    print(f"package:  {receipt.package_dir}")
    if receipt.zip_path:
        print(f"zip:      {receipt.zip_path}")
    print(f"verdict:  {'SEALED-VALID' if receipt.verdict else 'SEALED-WITH-FAILURES'}")
    for failure in receipt.failures:
        print(f"  failure: {failure}")
    return 0 if receipt.verdict else 1


if __name__ == "__main__":
    sys.exit(main())
