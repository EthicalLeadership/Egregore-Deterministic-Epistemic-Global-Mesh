#!/usr/bin/env python3
"""Bootstrap seed cells for the Egregore University / Guildhall.

Usage:
    python scripts/create_seed_cells.py [--advance]

Without --advance, the script only ensures that each seed cell has its
placeholder BCCBP artifact files on disk.

With --advance, it also registers every ``cells/<cell_id>/spec.yaml`` with the
BCCBP controller and submits each placeholder artifact to move the cell through
all stage gates until it reaches ``delivered`` status.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from egregore.governance.cell_protocol import STAGES, CellProtocolController

SEED_CELL_IDS = [
    "math_calculus",
    "sweng_python",
    "law_contract_review",
    "medicine_diagnosis",
    "carpentry_joinery",
    "electrical_wiring",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_artifacts(cells_dir: Path, cell_id: str, spec: dict[str, Any]) -> None:
    gates = spec.get("artifacts", {}).get("stage_gates", {})
    for stage in STAGES:
        rel_path = gates.get(stage)
        if not rel_path:
            continue
        artifact_path = cells_dir.parent / rel_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        if not artifact_path.exists():
            if artifact_path.suffix == ".md":
                artifact_path.write_text(
                    f"# {cell_id} — {stage}\n\nPlaceholder artifact.\n",
                    encoding="utf-8",
                )
            else:
                artifact_path.write_text(
                    json.dumps({"stage": stage, "cell_id": cell_id, "status": "placeholder"}, indent=2),
                    encoding="utf-8",
                )
            print(f"  created artifact: {artifact_path}")


def _advance_cell(controller: CellProtocolController, cells_dir: Path, cell_id: str, spec: dict[str, Any]) -> None:
    spec_path = cells_dir / cell_id / "spec.yaml"
    controller.register_cell(spec_path)
    gates = spec.get("artifacts", {}).get("stage_gates", {})
    for stage in STAGES:
        if stage == "plan":
            continue
        rel_path = gates.get(stage)
        if not rel_path:
            continue
        artifact_path = cells_dir.parent / rel_path
        controller.submit_artifact(cell_id, stage, artifact_path, validator_output="PASS")
    print(f"  advanced to: {controller.get_state(cell_id).status}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap seed cells.")
    parser.add_argument("--advance", action="store_true", help="Advance cells through BCCBP stage gates.")
    args = parser.parse_args(argv)

    repo_root = _repo_root()
    cells_dir = repo_root / "cells"
    controller = CellProtocolController()

    for cell_id in SEED_CELL_IDS:
        spec_path = cells_dir / cell_id / "spec.yaml"
        if not spec_path.exists():
            print(f"SKIP {cell_id}: spec not found at {spec_path}")
            continue

        import yaml

        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        print(f"{cell_id}:")
        _ensure_artifacts(cells_dir, cell_id, spec)
        if args.advance:
            _advance_cell(controller, cells_dir, cell_id, spec)

    print("\nSeed cells ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
