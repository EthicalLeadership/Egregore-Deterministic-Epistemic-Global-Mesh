"""Sealed evidence package verifier — independent, fail-closed.

Verifies a package trusting nothing inside it: fixity is recomputed from
the payload bytes, evidence checks (chain linkage + signatures, block
chain, custody continuity, witness quorum, anchor TSA) are re-run, the
embedded report's verdict is compared against the independently derived
one, and the report's detached signature is verified against the embedded
signer fingerprint.

Also hosts the chain-audit helpers shared with the sealer
(:mod:`egregore.application.evidence_sealer`).
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from egregore.domain.custody import validate_custody_continuity, CustodyEvent, CustodyError
from egregore.domain.evidence_package import (
    BAGIT_TXT,
    MANIFEST_NAME,
    TAG_FILES,
    TAGMANIFEST_NAME,
    EvidenceVerificationReport,
    parse_manifest,
)
from egregore.domain.witness_checkpoint import (
    Cosignature,
    WitnessCheckpoint,
    WitnessError,
    validate_quorum,
)
from egregore.kernel.ed25519_signer import verify_message
from egregore.shared.canonical import canonical_json, canonical_loads, sha256_hex

GENESIS_HASH = "0" * 64


# ---------------------------------------------------------------------------
# Chain-audit helpers (shared with the sealer)
# ---------------------------------------------------------------------------


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_zarc_lines(
    lines: Sequence[str], verify_key_hex: str | None = None
) -> dict[str, Any]:
    """Verify prev-hash linkage and (optionally) per-line signatures."""
    result: dict[str, Any] = {
        "entries": 0,
        "head_hash": GENESIS_HASH,
        "linkage_valid": True,
        "signatures_valid": None if verify_key_hex is None else True,
        "failures": [],
    }
    prev = GENESIS_HASH
    for index, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        result["entries"] += 1
        try:
            obj = canonical_loads(line)
        except Exception:
            result["linkage_valid"] = False
            result["failures"].append(f"line {index}: unparseable")
            break
        if str(obj.get("prev_hash")) != prev:
            result["linkage_valid"] = False
            result["failures"].append(f"line {index}: chain break")
            break
        if verify_key_hex is not None:
            sigless = {k: v for k, v in obj.items() if k != "sig"}
            unsigned = canonical_json(sigless).encode("utf-8")
            if not verify_message(
                verify_key_hex=verify_key_hex,
                message=unsigned,
                signature_hex=str(obj.get("sig", "")),
            ):
                result["signatures_valid"] = False
                result["failures"].append(f"line {index}: bad signature")
                break
        prev = sha256_hex((line + "\n").encode("utf-8"))
    result["head_hash"] = prev
    result["chain_valid"] = result["linkage_valid"] and result["signatures_valid"] in (
        True,
        None,
    )
    return result


def audit_block_lines(
    lines: Sequence[str], verify_key_hex: str | None = None
) -> dict[str, Any]:
    """Verify block-chain linkage and (optionally) block signatures."""
    result: dict[str, Any] = {
        "entries": 0,
        "linkage_valid": True,
        "signatures_valid": None if verify_key_hex is None else True,
        "failures": [],
    }
    prev = GENESIS_HASH
    for index, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        result["entries"] += 1
        try:
            block = canonical_loads(line)
        except Exception:
            result["linkage_valid"] = False
            result["failures"].append(f"block {index}: unparseable")
            break
        if str(block.get("previous_block_hash")) != prev:
            result["linkage_valid"] = False
            result["failures"].append(f"block {index}: chain break")
            break
        integrity = str(block.get("integrity_hash") or "")
        signature = str(block.get("block_signature") or "")
        if verify_key_hex is not None and signature:
            if not verify_message(
                verify_key_hex=verify_key_hex,
                message=integrity.encode("utf-8"),
                signature_hex=signature,
            ):
                result["signatures_valid"] = False
                result["failures"].append(f"block {index}: bad signature")
                break
        prev = integrity or prev
    result["chain_valid"] = result["linkage_valid"] and result["signatures_valid"] in (
        True,
        None,
    )
    return result


def extract_custody_events(lines: Sequence[str]) -> dict[str, list[CustodyEvent]]:
    """Group custody events by evidence_id from raw .zarc lines."""
    grouped: dict[str, list[CustodyEvent]] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = canonical_loads(line)
        except Exception:
            continue
        if obj.get("engine") != "custody":
            continue
        payload = obj.get("payload") or {}
        evidence_id = payload.get("evidence_id")
        if not evidence_id:
            continue
        grouped.setdefault(str(evidence_id), []).append(
            CustodyEvent.from_payload(payload)
        )
    return grouped


def extract_witness_checkpoints(lines: Sequence[str]) -> list[WitnessCheckpoint]:
    checkpoints: list[WitnessCheckpoint] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = canonical_loads(line)
        except Exception:
            continue
        if obj.get("engine") != "witness" or obj.get("event") != "witness.checkpoint":
            continue
        payload = obj.get("payload") or {}
        checkpoints.append(
            WitnessCheckpoint(
                checkpoint_id=str(payload["checkpoint_id"]),
                chain_head_hash=str(payload["chain_head_hash"]),
                entry_count=int(payload["entry_count"]),
                entries_merkle_root=str(payload["entries_merkle_root"]),
                timestamp_ns=int(payload["timestamp_ns"]),
                origin_fingerprint=str(payload["origin_fingerprint"]),
                origin_signature=str(payload["origin_signature"]),
                cosignatures=tuple(
                    Cosignature(
                        node_id=str(c["node_id"]),
                        fingerprint=str(c["fingerprint"]),
                        signature=str(c["signature"]),
                        timestamp_ns=int(c["timestamp_ns"]),
                    )
                    for c in payload.get("cosignatures", [])
                ),
            )
        )
    return checkpoints


def audit_custody(grouped: Mapping[str, list[CustodyEvent]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "evidence_ids": sorted(grouped),
        "continuity_valid": True,
        "failures": [],
    }
    for evidence_id, events in grouped.items():
        try:
            validate_custody_continuity(events)
        except CustodyError as exc:
            result["continuity_valid"] = False
            result["failures"].append(f"{evidence_id}: {exc}")
    return result


def audit_witness(
    checkpoints: Sequence[WitnessCheckpoint],
    *,
    min_witnesses: int,
    trusted_fingerprints: Mapping[str, str] | None,
    verify_key_hex: str | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "checkpoints": len(checkpoints),
        "quorum_assessed": bool(trusted_fingerprints) and verify_key_hex is not None,
        "quorum_valid": None,
        "failures": [],
    }
    if not result["quorum_assessed"]:
        return result
    result["quorum_valid"] = True

    def _verify(payload_hash: str, signature: str, fingerprint: str) -> bool:
        return verify_message(
            verify_key_hex=fingerprint,
            message=payload_hash.encode("utf-8"),
            signature_hex=signature,
        )

    for checkpoint in checkpoints:
        try:
            validate_quorum(
                checkpoint,
                min_witnesses=min_witnesses,
                trusted_fingerprints=trusted_fingerprints or {},
                verify=_verify,
            )
        except WitnessError as exc:
            result["quorum_valid"] = False
            result["failures"].append(str(exc))
    return result


def audit_anchors(
    anchors: Sequence[Mapping[str, Any]],
    tsa_trust_dir: Path | None,
    tsa_verify: Any | None = None,
) -> dict[str, Any]:
    """Audit anchor records.

    ``tsa_verify`` is the injected TSA verification callable (signature of
    ``infrastructure.tsa_verifier.verify_tsa_token``). It is injected
    rather than imported so the application layer stays free of
    infrastructure dependencies (arch-enforced). Without it, tier-2
    anchors are unassessed — never silently verified.
    """
    result: dict[str, Any] = {
        "total": len(anchors),
        "tsa_verified": 0,
        "tsa_failed": 0,
        "local": 0,
        "mock": 0,
        "trust_assessed": tsa_trust_dir is not None and tsa_verify is not None,
        "failures": [],
    }
    if not result["trust_assessed"]:
        result["local"] = sum(1 for a in anchors if str(a.get("tier")) == "1")
        result["mock"] = sum(1 for a in anchors if str(a.get("tier")) not in ("1", "2"))
        result["tsa_failed"] = sum(1 for a in anchors if str(a.get("tier")) == "2")
        return result

    for anchor in anchors:
        tier = str(anchor.get("tier"))
        if tier == "2":
            try:
                report = tsa_verify(
                    token_bytes=bytes.fromhex(str(anchor["notarization"])),
                    expected_hash_hex=str(anchor["block_hash"]),
                    nonce=None,
                    trust_dir=tsa_trust_dir,
                )
                if report.verdict:
                    result["tsa_verified"] += 1
                else:
                    result["tsa_failed"] += 1
                    result["failures"].append(
                        f"anchor {anchor.get('anchor_id')}: "
                        + "; ".join(report.failures)
                    )
            except Exception as exc:
                result["tsa_failed"] += 1
                result["failures"].append(
                    f"anchor {anchor.get('anchor_id')}: unparseable token: {exc}"
                )
        elif tier == "1":
            result["local"] += 1
        else:
            result["mock"] += 1
    return result


# ---------------------------------------------------------------------------
# Package verification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PackageVerificationResult:
    verdict: bool
    checks: Mapping[str, bool]
    failures: tuple[str, ...] = ()
    report: Mapping[str, Any] | None = None

    def to_canonical(self) -> dict[str, Any]:
        return {
            "__type__": "PackageVerificationResult",
            "verdict": self.verdict,
            "checks": dict(sorted(self.checks.items())),
            "failures": list(self.failures),
            "report": dict(self.report) if self.report else None,
        }


def _verify_report_signature(report_obj: Mapping[str, Any], sig_obj: Mapping[str, Any]) -> bool:
    fingerprint = str(sig_obj.get("fingerprint", ""))
    signature = str(sig_obj.get("signature", ""))
    if not fingerprint or not signature:
        return False
    canonical = dict(report_obj)
    payload_hash = sha256_hex(canonical_json(canonical).encode("utf-8"))
    return verify_message(
        verify_key_hex=fingerprint,
        message=payload_hash.encode("utf-8"),
        signature_hex=signature,
    )


def verify_package(
    path: Path,
    *,
    tsa_trust_dir: Path | None = None,
    min_witnesses: int = 0,
    trusted_fingerprints: Mapping[str, str] | None = None,
    tsa_verify: Any | None = None,
) -> PackageVerificationResult:
    """Independently verify a sealed package (directory or .zip)."""
    path = Path(path)
    if not path.exists():
        return PackageVerificationResult(
            verdict=False, checks={}, failures=(f"package not found: {path}",)
        )
    if zipfile.is_zipfile(path):
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(path) as archive:
                archive.extractall(tmp)
            roots = [p for p in Path(tmp).iterdir() if p.is_dir()]
            root = roots[0] if len(roots) == 1 else Path(tmp)
            return _verify_package_dir(
                root,
                tsa_trust_dir=tsa_trust_dir,
                min_witnesses=min_witnesses,
                trusted_fingerprints=trusted_fingerprints,
                tsa_verify=tsa_verify,
            )
    return _verify_package_dir(
        path,
        tsa_trust_dir=tsa_trust_dir,
        min_witnesses=min_witnesses,
        trusted_fingerprints=trusted_fingerprints,
        tsa_verify=tsa_verify,
    )


def _verify_package_dir(
    root: Path,
    *,
    tsa_trust_dir: Path | None,
    min_witnesses: int,
    trusted_fingerprints: Mapping[str, str] | None,
    tsa_verify: Any | None,
) -> PackageVerificationResult:
    checks: dict[str, bool] = {}
    failures: list[str] = []

    # a. BagIt declaration + manifest presence
    bagit = root / BAGIT_TXT
    checks["bagit_declaration"] = bagit.exists() and "BagIt-Version: 1.0" in bagit.read_text(
        encoding="utf-8", errors="replace"
    )
    if not checks["bagit_declaration"]:
        failures.append("missing or malformed bagit.txt")
    manifest_path = root / MANIFEST_NAME
    tagmanifest_path = root / TAGMANIFEST_NAME
    checks["manifests_present"] = manifest_path.exists() and tagmanifest_path.exists()
    if not checks["manifests_present"]:
        failures.append("missing manifest-sha256.txt or tagmanifest-sha256.txt")
    if not checks["manifests_present"]:
        return PackageVerificationResult(
            verdict=False, checks=checks, failures=tuple(failures)
        )

    # b. Fixity
    manifest = parse_manifest(manifest_path.read_text(encoding="utf-8"))
    payload_ok = True
    for relpath, expected in manifest.items():
        target = root / relpath
        if not target.exists():
            payload_ok = False
            failures.append(f"missing payload file: {relpath}")
            continue
        actual = hash_file(target)
        if actual != expected:
            payload_ok = False
            failures.append(f"fixity mismatch: {relpath}")
    data_dir = root / "data"
    if data_dir.is_dir():
        for file in data_dir.rglob("*"):
            if file.is_file():
                rel = file.relative_to(root).as_posix()
                if rel not in manifest:
                    payload_ok = False
                    failures.append(f"unmanifested payload file: {rel}")
    checks["payload_fixity"] = payload_ok

    tagmanifest = parse_manifest(tagmanifest_path.read_text(encoding="utf-8"))
    tag_ok = True
    for relpath, expected in tagmanifest.items():
        target = root / relpath
        if not target.exists() or hash_file(target) != expected:
            tag_ok = False
            failures.append(f"tag fixity mismatch: {relpath}")
    checks["tag_fixity"] = tag_ok

    # Report + signature
    report_path = root / "verification_report.json"
    sig_path = root / "verification_report.sig"
    report_obj: Mapping[str, Any] | None = None
    if report_path.exists() and sig_path.exists():
        try:
            report_obj = canonical_loads(report_path.read_bytes())
            sig_obj = json.loads(sig_path.read_text(encoding="utf-8"))
            checks["report_signature"] = _verify_report_signature(report_obj, sig_obj)
            if not checks["report_signature"]:
                failures.append("verification report signature invalid")
        except Exception as exc:
            checks["report_signature"] = False
            failures.append(f"verification report unparseable: {exc}")
    else:
        checks["report_signature"] = False
        failures.append("missing verification report or signature")

    signer_key = None
    if report_obj is not None:
        signer_key = str(report_obj.get("signer_fingerprint") or "") or None

    # c. Independent evidence verification
    evidence_ok = True
    chains_dir = data_dir / "chains"
    chain_results: list[dict[str, Any]] = []
    if chains_dir.is_dir():
        for chain_file in sorted(chains_dir.glob("*.zarc")):
            audit = audit_zarc_lines(
                chain_file.read_text(encoding="utf-8").splitlines(),
                verify_key_hex=signer_key,
            )
            audit["name"] = chain_file.name
            chain_results.append(audit)
            if not audit["chain_valid"]:
                evidence_ok = False
                failures.extend(f"chain {chain_file.name}: {f}" for f in audit["failures"])

    blocks_file = data_dir / "blocks" / "blocks.zarc"
    blocks_result = None
    if blocks_file.exists():
        blocks_result = audit_block_lines(
            blocks_file.read_text(encoding="utf-8").splitlines(),
            verify_key_hex=signer_key,
        )
        if not blocks_result["chain_valid"]:
            evidence_ok = False
            failures.extend(f"blocks: {f}" for f in blocks_result["failures"])

    custody_result = None
    witness_result = None
    all_chain_lines: list[str] = []
    if chains_dir.is_dir():
        for chain_file in sorted(chains_dir.glob("*.zarc")):
            all_chain_lines.extend(chain_file.read_text(encoding="utf-8").splitlines())
    grouped = extract_custody_events(all_chain_lines)
    if grouped:
        custody_result = audit_custody(grouped)
        if not custody_result["continuity_valid"]:
            evidence_ok = False
            failures.extend(f"custody: {f}" for f in custody_result["failures"])
    checkpoints = extract_witness_checkpoints(all_chain_lines)
    if checkpoints:
        witness_result = audit_witness(
            checkpoints,
            min_witnesses=min_witnesses,
            trusted_fingerprints=trusted_fingerprints,
            verify_key_hex=signer_key,
        )
        if witness_result["quorum_valid"] is False:
            evidence_ok = False
            failures.extend(f"witness: {f}" for f in witness_result["failures"])

    anchors_file = data_dir / "anchors" / "anchors.json"
    anchors_result = None
    if anchors_file.exists():
        anchors = canonical_loads(anchors_file.read_bytes())
        anchors_result = audit_anchors(anchors, tsa_trust_dir, tsa_verify)
        if anchors_result["tsa_failed"]:
            evidence_ok = False
            failures.extend(f"anchors: {f}" for f in anchors_result["failures"])
    checks["evidence_independent"] = evidence_ok

    # d. Embedded verdict consistency
    if report_obj is not None:
        embedded_verdict = bool(report_obj.get("verdict"))
        independent_ok = (
            payload_ok
            and evidence_ok
            and (signer_key is None or checks["report_signature"])
        )
        checks["verdict_consistency"] = embedded_verdict == independent_ok
        if not checks["verdict_consistency"]:
            failures.append(
                f"embedded report verdict ({embedded_verdict}) disagrees with "
                f"independent verification ({independent_ok})"
            )
    else:
        checks["verdict_consistency"] = False

    # e. Subject index consistency
    index_path = root / "subject_index.json"
    if index_path.exists():
        try:
            index = canonical_loads(index_path.read_bytes())
            entry_counts = {c["name"]: c["entries"] for c in chain_results}
            index_ok = True
            for ref in index.get("entry_refs", []):
                chain_name = ref.get("chain")
                line_index = int(ref.get("line_index", -1))
                if chain_name not in entry_counts or not (
                    0 <= line_index < entry_counts[chain_name]
                ):
                    index_ok = False
                    failures.append(
                        f"subject index ref out of range: {ref}"
                    )
            checks["subject_index"] = index_ok
        except Exception as exc:
            checks["subject_index"] = False
            failures.append(f"subject index unparseable: {exc}")
    else:
        checks["subject_index"] = True  # optional component

    return PackageVerificationResult(
        verdict=all(checks.values()),
        checks=checks,
        failures=tuple(failures),
        report=dict(report_obj) if report_obj else None,
    )
