#!/usr/bin/env python3
"""Bootstrap the Egregore Cognitive Civilization Build Protocol (BCCBP).

This script:
1. Validates the Universal Cell Schema exists.
2. Registers the sweng_python cell in the stage-gate controller.
3. Initializes the RAG knowledge base.
4. Prints the University state.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("/opt/egregore")
sys.path.insert(0, str(ROOT / "src"))

from egregore.governance.cell_protocol import CellProtocolController


def main() -> int:
    print("=" * 60)
    print("Egregore Cognitive Civilization Build Protocol — Bootstrap")
    print("=" * 60)

    # 1. Validate schema
    schema_path = ROOT / "schemas" / "cell_spec.schema.yaml"
    if not schema_path.exists():
        print(f"ERROR: Schema not found: {schema_path}")
        return 1
    print(f"[OK] Schema found: {schema_path}")

    # 2. Register first cell
    spec_path = ROOT / "cells" / "sweng_python" / "spec.yaml"
    if not spec_path.exists():
        print(f"ERROR: Cell spec not found: {spec_path}")
        return 1

    ctrl = CellProtocolController()
    try:
        state = ctrl.register_cell(spec_path)
        print(f"[OK] Registered cell: {state.cell_id} v{state.version}")
        print(f"[OK] Taxonomy: {state.taxonomy}")
        print(f"[OK] Current stage: {state.current_stage}")
        print(f"[OK] Overall status: {state.status}")
    except Exception as exc:
        print(f"ERROR: Failed to register cell: {exc}")
        return 1

    # 3. Initialize RAG
    print("\n[--] Initializing RAG knowledge base...")
    import subprocess
    rag_script = ROOT / "scripts" / "init_rag.py"
    result = subprocess.run([sys.executable, str(rag_script)], cwd=str(ROOT), capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: RAG initialization failed:\n{result.stderr}")
        return 1
    print(result.stdout)

    # 4. Print University state
    print("\n[--] University registry state:")
    for cell in ctrl.list_cells():
        completed = sum(
            1 for s in cell.stage_states.values()
            if s.get("status") == "completed"
        )
        total = len(cell.stage_states)
        print(f"  - {cell.cell_id} ({cell.taxonomy}): {cell.current_stage} [{completed}/{total}]")

    print("\n" + "=" * 60)
    print("Bootstrap complete. Next:")
    print("  1. Produce architecture.dot for sweng_python (DRAW stage).")
    print("  2. Submit it via controller.submit_artifact('sweng_python', 'draw', path).")
    print("  3. Wire Ombudsman and RAG routers into egregore.interface.bootstrap.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
