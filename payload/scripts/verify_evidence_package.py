#!/usr/bin/env python3
"""Verify a sealed evidence package (directory or .zip). Fail-closed.

Usage:
    python scripts/verify_evidence_package.py <package> \
        [--tsa-trust-dir DIR] [--min-witnesses K] [--json]

Exit codes: 0 = verified, 1 = verification failed, 2 = package not found.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from egregore.application.evidence_package_verifier import verify_package
from egregore.shared.canonical import canonical_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", help="package directory or .zip")
    parser.add_argument("--tsa-trust-dir", default=None)
    parser.add_argument("--min-witnesses", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    from egregore.infrastructure.tsa_verifier import verify_tsa_token

    result = verify_package(
        Path(args.package),
        tsa_trust_dir=Path(args.tsa_trust_dir) if args.tsa_trust_dir else None,
        min_witnesses=args.min_witnesses,
        tsa_verify=verify_tsa_token,
    )
    if args.json:
        print(canonical_json(result.to_canonical()))
    else:
        for check, ok in sorted(result.checks.items()):
            print(f"  {'OK  ' if ok else 'FAIL'} {check}")
        for failure in result.failures:
            print(f"  violation: {failure}")
        print(
            f"package {'VERIFIED' if result.verdict else 'FAILED'}: {args.package}"
        )
    if not result.checks:
        return 2
    return 0 if result.verdict else 1


if __name__ == "__main__":
    sys.exit(main())
