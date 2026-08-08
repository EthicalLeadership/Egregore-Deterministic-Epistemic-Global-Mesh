"""Evidence sealer — assembles and signs sealed evidence packages.

Produces the BagIt-profile package documented in
``docs/evidence/sealed_packages.md``: whole-chain payload + subject index +
fixity manifests + a signed verification report. The sealer runs the same
audit helpers as the independent verifier
(:mod:`egregore.application.evidence_package_verifier`), so the embedded
report reflects exactly what an auditor will re-derive.

Fail-closed policy: a failing verification suite does not abort sealing —
it is recorded in the report (``verdict: false`` with enumerated failures)
and the signature attests *that* content. Sealing is refused only for
missing inputs or a missing signing backend.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from egregore.application.evidence_package_verifier import (
    audit_anchors,
    audit_block_lines,
    audit_custody,
    audit_witness,
    audit_zarc_lines,
    extract_custody_events,
    extract_witness_checkpoints,
    hash_file,
)
from egregore.domain.evidence_package import (
    BAG_INFO_TXT,
    BAGIT_TXT,
    MANIFEST_NAME,
    REPORT_NAME,
    REPORT_SIG_NAME,
    SUBJECT_INDEX_NAME,
    TAGMANIFEST_NAME,
    TAG_FILES,
    EvidencePackageError,
    EvidenceVerificationReport,
    derive_seal_id,
    format_manifest,
    payload_oxum,
)
from egregore.shared.canonical import canonical_json, canonical_loads, sha256_hex

REPORT_VERSION = "1.0"


class ISigningBackend(Protocol):
    def fingerprint(self) -> str: ...
    def sign(self, payload_hash: str) -> str: ...
    def verify(self, payload_hash: str, signature: str, fingerprint: str) -> bool: ...


@dataclass(frozen=True)
class SealReceipt:
    seal_id: str
    package_dir: str
    zip_path: str | None
    verdict: bool
    failures: tuple[str, ...]

    def to_canonical(self) -> dict[str, Any]:
        return {
            "__type__": "SealReceipt",
            "seal_id": self.seal_id,
            "package_dir": self.package_dir,
            "zip_path": self.zip_path,
            "verdict": self.verdict,
            "failures": list(self.failures),
        }


class EvidenceSealer:
    """Assembles sealed evidence packages."""

    def __init__(self, *, signing_backend: ISigningBackend) -> None:
        if signing_backend is None:
            raise EvidencePackageError("A signing backend is required to seal")
        self._backend = signing_backend

    def seal(
        self,
        output_dir: Path,
        *,
        chains: Mapping[str, Path],
        subject: Mapping[str, str],
        timestamp_ns: int,
        block_store_path: Path | None = None,
        anchors: list[Mapping[str, Any]] | None = None,
        provenance: Any | None = None,
        tsa_trust_dir: Path | None = None,
        min_witnesses: int = 0,
        trusted_fingerprints: Mapping[str, str] | None = None,
        custody_evidence_id: str | None = None,
        custody_actor: str = "evidence-sealer",
        custody_role: str = "system",
        make_zip: bool = False,
        tsa_verify: Any | None = None,
    ) -> SealReceipt:
        output_dir = Path(output_dir)
        if not chains:
            raise EvidencePackageError("At least one chain is required")
        for name, chain_path in chains.items():
            if not Path(chain_path).exists():
                raise EvidencePackageError(f"Chain not found: {name}={chain_path}")
        if output_dir.exists() and any(output_dir.iterdir()):
            raise EvidencePackageError(
                f"Output directory is not empty: {output_dir}"
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        data_dir = output_dir / "data"

        # 1. Payload: chains + blocks
        chain_lines: dict[str, list[str]] = {}
        payload_sizes: dict[str, int] = {}
        for name, chain_path in sorted(chains.items()):
            rel = f"data/chains/{name}.zarc"
            target = output_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(chain_path, target)
            chain_lines[name] = target.read_text(encoding="utf-8").splitlines()
            payload_sizes[rel] = target.stat().st_size

        if block_store_path is not None and Path(block_store_path).exists():
            rel = "data/blocks/blocks.zarc"
            target = output_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(block_store_path, target)
            payload_sizes[rel] = target.stat().st_size

        # 2. Anchors export
        if anchors:
            rel = "data/anchors/anchors.json"
            target = output_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(canonical_json(list(anchors)), encoding="utf-8")
            payload_sizes[rel] = target.stat().st_size

        # 3. Custody + witness exports from the live chain (when provided)
        custody_grouped: dict[str, Any] = {}
        checkpoints: list[Any] = []
        if provenance is not None:
            live_lines = list(provenance.iter_lines())
            custody_grouped = extract_custody_events(live_lines)
            checkpoints = extract_witness_checkpoints(live_lines)
            for evidence_id, events in sorted(custody_grouped.items()):
                rel = f"data/custody/{evidence_id}.json"
                target = output_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    canonical_json([e.to_payload() for e in events]),
                    encoding="utf-8",
                )
                payload_sizes[rel] = target.stat().st_size
            if checkpoints:
                rel = "data/witness/checkpoints.json"
                target = output_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    canonical_json([c.to_payload() for c in checkpoints]),
                    encoding="utf-8",
                )
                payload_sizes[rel] = target.stat().st_size

        # 4. Verification suite (same helpers the verifier uses)
        fingerprint = self._backend.fingerprint()
        failures: list[str] = []
        chain_results: list[dict[str, Any]] = []
        all_valid = True
        for name, lines in sorted(chain_lines.items()):
            audit = audit_zarc_lines(lines, verify_key_hex=fingerprint)
            audit["name"] = f"{name}.zarc"
            chain_results.append(audit)
            if not audit["chain_valid"]:
                all_valid = False
                failures.extend(f"chain {name}: {f}" for f in audit["failures"])

        blocks_result = None
        blocks_target = data_dir / "blocks" / "blocks.zarc"
        if blocks_target.exists():
            blocks_result = audit_block_lines(
                blocks_target.read_text(encoding="utf-8").splitlines(),
                verify_key_hex=fingerprint,
            )
            if not blocks_result["chain_valid"]:
                all_valid = False
                failures.extend(f"blocks: {f}" for f in blocks_result["failures"])

        anchors_result = None
        if anchors:
            anchors_result = audit_anchors(anchors, tsa_trust_dir, tsa_verify)
            if anchors_result["tsa_failed"]:
                all_valid = False
                failures.extend(f"anchors: {f}" for f in anchors_result["failures"])

        custody_result = None
        if custody_grouped:
            custody_result = audit_custody(custody_grouped)
            if not custody_result["continuity_valid"]:
                all_valid = False
                failures.extend(f"custody: {f}" for f in custody_result["failures"])

        witness_result = None
        if checkpoints:
            witness_result = audit_witness(
                checkpoints,
                min_witnesses=min_witnesses,
                trusted_fingerprints=trusted_fingerprints,
                verify_key_hex=fingerprint,
            )
            if witness_result["quorum_valid"] is False:
                all_valid = False
                failures.extend(f"witness: {f}" for f in witness_result["failures"])

        # 5. Fixity manifest (seal identity derives from it)
        manifest_entries = {
            rel: hash_file(output_dir / rel) for rel in sorted(payload_sizes)
        }
        manifest_text = format_manifest(manifest_entries)
        seal_id = derive_seal_id(manifest_text)

        # 6. Report (carries seal_id) + one signature over its canonical bytes
        report = EvidenceVerificationReport(
            report_version=REPORT_VERSION,
            seal_id=seal_id,
            generated_at_ns=timestamp_ns,
            subject=dict(subject),
            chains=tuple(chain_results),
            blocks=blocks_result,
            anchors=anchors_result,
            custody=custody_result,
            witness=witness_result,
            fixity_entries=len(manifest_entries),
            verdict=all_valid,
            failures=tuple(failures),
            signer_fingerprint=fingerprint,
        )
        report_payload_hash = sha256_hex(
            canonical_json(report.to_canonical()).encode("utf-8")
        )
        report_signature = self._backend.sign(report_payload_hash)

        (output_dir / REPORT_NAME).write_text(
            canonical_json(report.to_canonical()), encoding="utf-8"
        )
        (output_dir / REPORT_SIG_NAME).write_text(
            json.dumps(
                {
                    "algorithm": "ed25519",
                    "signed_payload": "sha256(canonical_json(verification_report.json))",
                    "fingerprint": fingerprint,
                    "signature": report_signature,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        # 7. Subject index
        entry_refs: list[dict[str, Any]] = []
        for name, lines in sorted(chain_lines.items()):
            for index, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = canonical_loads(line)
                except Exception:
                    continue
                payload = obj.get("payload") or {}
                for key, value in subject.items():
                    if str(payload.get(key, "")) == str(value):
                        entry_refs.append(
                            {
                                "chain": f"{name}.zarc",
                                "line_index": index,
                                "subject_key": key,
                                "subject_value": str(value),
                            }
                        )
        subject_index = {
            "__type__": "EvidenceSubjectIndex",
            "seal_id": seal_id,
            "subject": dict(subject),
            "chains": {
                f"{name}.zarc": {
                    "entries": len([ln for ln in lines if ln.strip()]),
                    "head_hash": sha256_hex(
                        (
                            [ln for ln in lines if ln.strip()][-1] + "\n"
                        ).encode("utf-8")
                    )
                    if any(ln.strip() for ln in lines)
                    else "0" * 64,
                }
                for name, lines in sorted(chain_lines.items())
            },
            "entry_refs": entry_refs,
        }
        (output_dir / SUBJECT_INDEX_NAME).write_text(
            canonical_json(subject_index), encoding="utf-8"
        )

        # 8. bagit.txt + bag-info.txt (identity layer: roles/entity only)
        (output_dir / BAGIT_TXT).write_text(
            "BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n",
            encoding="utf-8",
        )
        (output_dir / BAG_INFO_TXT).write_text(
            "Source-Organization: Egregor\n"
            "Contact-Role: Egregore Curator\n"
            f"External-Identifier: {seal_id}\n"
            f"Bagging-Timestamp-Ns: {timestamp_ns}\n"
            f"Payload-Oxum: {payload_oxum(payload_sizes)}\n",
            encoding="utf-8",
        )

        # 9. Manifests
        (output_dir / MANIFEST_NAME).write_text(manifest_text, encoding="utf-8")
        tag_entries = {
            name: hash_file(output_dir / name)
            for name in TAG_FILES
            if (output_dir / name).exists()
        }
        (output_dir / TAGMANIFEST_NAME).write_text(
            format_manifest(tag_entries), encoding="utf-8"
        )

        # 10. Custody seal event on the live chain (after seal_id exists)
        if provenance is not None and custody_evidence_id is not None:
            from egregore.application.custody_log import CustodyLog
            from egregore.domain.custody import create_custody_event

            CustodyLog(provenance).record(
                create_custody_event(
                    evidence_id=custody_evidence_id,
                    action="seal",
                    actor=custody_actor,
                    role=custody_role,
                    timestamp_ns=timestamp_ns,
                    evidence_hash=seal_id,
                    purpose=f"sealed evidence package {seal_id[:16]}",
                )
            )

        # 11. Optional deterministic zip
        zip_path: str | None = None
        if make_zip:
            zip_file = output_dir.with_suffix(".zip")
            with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_DEFLATED) as archive:
                for file in sorted(output_dir.rglob("*")):
                    if file.is_file():
                        arcname = f"{output_dir.name}/{file.relative_to(output_dir).as_posix()}"
                        info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
                        info.compress_type = zipfile.ZIP_DEFLATED
                        archive.writestr(info, file.read_bytes())
            zip_path = str(zip_file)

        return SealReceipt(
            seal_id=seal_id,
            package_dir=str(output_dir),
            zip_path=zip_path,
            verdict=all_valid,
            failures=tuple(failures),
        )
