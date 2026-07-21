#!/usr/bin/env python3
"""Egregore SEL-X Audit — measures closure against the SEL-X spec."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path.home() / "egregore"
SRC = REPO / "src" / "egregore"


def exists_any(patterns: List[str], root: Path = SRC) -> bool:
    for p in patterns:
        if list(root.rglob(p)):
            return True
    return False


def grep_exists(pattern: str, root: Path = SRC, glob: str = "*.py") -> bool:
    from subprocess import run
    if root.is_file():
        cmd = f"grep -Eni '{pattern}' {root} | head -1"
    else:
        cmd = f"grep -Erni '{pattern}' {root}/ --include='{glob}' | head -1"
    return run(cmd, shell=True, capture_output=True, text=True, cwd=REPO).stdout.strip() != ""


def score_phase(name: str, checks: Dict[str, bool], required: Optional[List[str]] = None) -> Dict[str, Any]:
    required = required or list(checks.keys())
    passed = sum(1 for k in required if checks.get(k))
    total = len(required)
    pct = round(passed / total * 100, 1)
    status = "PASS" if pct >= 80 else "PARTIAL" if pct >= 50 else "FAIL"
    return {
        "phase": name,
        "status": status,
        "score": pct,
        "checks": checks,
    }


def audit() -> Dict[str, Any]:
    results: Dict[str, Any] = {}

    # Phase 0 — Stabilize
    p0_checks = {
        "execution_guard": exists_any(["application/execution_guard.py"]),
        "guard_policy": exists_any(["application/guard_policy.py"]),
        "validate_identity": grep_exists("validate_identity"),
        "validate_role": grep_exists("validate_role"),
        "validate_policy": grep_exists("validate_policy"),
        "validate_budget": grep_exists("validate_budget"),
        "validate_feature_flag": grep_exists("validate_feature_flag"),
        "tenant_id_in_execution_context": grep_exists("tenant_id", SRC / "domain" / "execution_context.py"),
        "budget_context": exists_any(["domain/execution_record.py"]),
    }
    results["phase_0_stabilize"] = score_phase("Phase 0 — Stabilize", p0_checks)

    # Phase 1 — Execution Records
    p1_checks = {
        "stable_ids": exists_any(["shared/stable_ids.py"]),
        "provenance": exists_any(["kernel/provenance.py"]),
        "execution_record": exists_any(["domain/execution_record.py"]),
        "policy_context": grep_exists("class PolicyContext"),
        "budget_context": grep_exists("class BudgetContext"),
        "previous_record_hash": grep_exists("previous_record_hash"),
        "integrity_hash": grep_exists("integrity_hash"),
    }
    results["phase_1_execution_records"] = score_phase("Phase 1 — Execution Records", p1_checks)

    # Phase 2 — Hashing / Signing / Policy / Tools / Models
    p2_checks = {
        "ed25519_signer": exists_any(["kernel/ed25519_signer.py"]),
        "encryption": exists_any(["infrastructure/encryption.py"]),
        "merkle": exists_any(["shared/merkle.py"]),
        "model_catalog": exists_any(["application/model_manager.py", "infrastructure/local_model_catalog.py"]),
        "policy_versioning": exists_any(["application/policy_versioning.py"]),
        "decision_hash": grep_exists("decision_hash"),
        "tool_registry": grep_exists("tool_registry|ToolRegistry"),
    }
    results["phase_2_crypto_policy_models"] = score_phase("Phase 2 — Crypto/Policy/Models", p2_checks)

    # Phase 3 — Micro-blocks + Causal Vectors
    p3_checks = {
        "execution_block": exists_any(["domain/execution_block.py"]),
        "block_builder": exists_any(["application/block_builder.py"]),
        "merkle_root": grep_exists("merkle_root"),
        "previous_block_hash": grep_exists("previous_block_hash", SRC / "domain" / "execution_block.py"),
        "causal_vector": grep_exists("CausalVector"),
        "vector_clock": grep_exists("VectorClock"),
        "block_store": exists_any(["infrastructure/block_store.py", "infrastructure/postgres_block_store.py"]),
    }
    results["phase_3_micro_blocks"] = score_phase("Phase 3 — Micro-blocks + Causality", p3_checks)

    # Phase 4 — Anchor Orchestrator + Replay + Key Mgmt + Fork
    p4_checks = {
        "replay": exists_any(["application/replay_determinism.py", "application/semantics_replay_interpreter.py"]),
        "key_management": exists_any(["infrastructure/key_management.py"]),
        "key_rotation": grep_exists("KeyRotationPolicy|rotate_key"),
        "freeze_controller": exists_any(["shared/freeze_state.py"]),
        "fork_detected": grep_exists("fork_detected"),
        "integrity_gate": exists_any(["governance/anchorum_integrity_gate.py"]),
    }
    results["phase_4_anchor_replay_key"] = score_phase("Phase 4 — Anchor/Replay/Key/Fork", p4_checks)

    # Phase 5 — Public Notarization
    p5_checks = {
        "anchor_orchestrator": exists_any(["services/anchor_orchestrator"]),
        "timestamp_client": exists_any(["services/anchor_orchestrator/timestamp_client.py"]),
        "rfc3161": grep_exists("RFC3161|rfc3161"),
        "local_fallback": grep_exists("LocalFallbackTimestampClient"),
        "anchor_record": grep_exists("class AnchorRecord"),
        "public_verify": grep_exists("public_verify"),
    }
    results["phase_5_public_notarization"] = score_phase("Phase 5 — Public Notarization", p5_checks)

    # Phase 6 — Federation
    p6_checks = {
        "federation_mesh": exists_any(["application/federation_mesh.py"]),
        "cross_sign": grep_exists("cross_sign"),
        "trust_mesh": grep_exists("trust_mesh"),
        "malicious_node_detection": grep_exists("malicious_node_detection"),
        "node_registry": exists_any(["application/node_registry.py"]),
        "public_key_fingerprint": grep_exists("public_key_fingerprint"),
    }
    results["phase_6_federation"] = score_phase("Phase 6 — Federation", p6_checks)

    # Overall
    scores = [v["score"] for v in results.values()]
    overall = round(sum(scores) / len(scores), 1)
    results["overall"] = {
        "score": overall,
        "status": "PASS" if overall >= 85 else "PARTIAL" if overall >= 63 else "FAIL",
        "phase_count": len(scores),
    }
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Egregore SEL-X audit")
    parser.add_argument("--format", choices=["console", "json"], default="console")
    args = parser.parse_args()

    results = audit()

    if args.format == "json":
        print(json.dumps(results, indent=2))
    else:
        print("=== BLACKSTAR SEL-X AUDIT ===")
        for key, value in results.items():
            if key == "overall":
                continue
            print(f"\n{value['phase']}: {value['status']} ({value['score']}%)")
            for check, ok in value["checks"].items():
                status = "OK" if ok else "MISSING"
                print(f"  [{status}] {check}")
        overall = results["overall"]
        print("\n" + "=" * 40)
        print(f"OVERALL: {overall['score']}% — {overall['status']}")

    return 0 if results["overall"]["status"] in ("PASS", "PARTIAL") else 1


if __name__ == "__main__":
    sys.exit(main())
