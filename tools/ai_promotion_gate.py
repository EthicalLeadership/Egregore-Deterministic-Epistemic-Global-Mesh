#!/usr/bin/env python3
"""Fail-closed promotion gate wrapper over EMS registry enforcement."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from egregore.ems.registry import build_registry_from_env


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify human approval before promotion")
    parser.add_argument("--model-id", required=True, help="Model family / promotion target")
    parser.add_argument("--artifact", required=True, help="Approval artifact JSON path")
    parser.add_argument("--signature", required=True, help="Detached signature hex path")
    parser.add_argument("--public-key", required=True, help="Approver public key hex path")
    parser.add_argument("--checkpoints-dir", required=True, help="Promotion checkpoints dir")
    args = parser.parse_args()

    registry = build_registry_from_env()
    try:
        approval = registry.promote(
            model_id=args.model_id,
            checkpoints_dir=args.checkpoints_dir,
            approval_artifact=args.artifact,
            approval_signature=args.signature,
            approval_public_key=args.public_key,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[PROMOTION_GATE][BLOCKED] {exc}")
        return 1

    print(
        f"[PROMOTION_GATE][PASS] Promotion approved for {approval['model_id']} "
        f"by {approval['approved_by']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
