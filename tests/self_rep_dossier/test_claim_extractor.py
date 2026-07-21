"""Unit tests for SelfRep claim extractor."""

from __future__ import annotations

from datetime import UTC, datetime

from egregore.domain.self_rep_dossier.claim_extractor import extract_claims_from_artifact
from egregore.domain.self_rep_dossier.dossier_models import Artifact


def _artifact(
    content: str = "", subject: str = "", filename: str = "test.txt"
) -> Artifact:
    return Artifact(
        artifact_id="a1b2c3",
        source_path=f"/tmp/{filename}",  # noqa: S108
        filename=filename,
        container_type="txt",
        modality="text",
        timestamp=datetime(2025, 5, 1, 12, 0, tzinfo=UTC),
        content_text=content,
        metadata={"subject": subject} if subject else {},
    )


def test_subject_claim_extracted():
    art = _artifact(subject="Request for medical accommodation denied")
    claims = extract_claims_from_artifact(art, "actor:claimant")
    assert any("Request for medical accommodation denied" in c.text for c in claims)


def test_body_claims_extracted():
    art = _artifact(
        content="The insurer denied the claim. The employer must accommodate."
    )
    claims = extract_claims_from_artifact(art, "actor:claimant")
    texts = [c.text for c in claims]
    assert any("denied" in t for t in texts)
    assert any("must accommodate" in t for t in texts)


def test_noise_filtered():
    art = _artifact(content="Cordialement, John. This is confidential.")
    claims = extract_claims_from_artifact(art, "actor:claimant")
    assert len(claims) == 0


def test_anomaly_claim_generated():
    art = _artifact(filename="doc.pdf")
    art = Artifact(
        artifact_id="a1b2c3",
        source_path="/tmp/doc.pdf",  # noqa: S108
        filename="doc.pdf",
        container_type="pdf",
        modality="document",
        timestamp=None,
        content_text="",
        anomalies=("[high_findings] Metadata scrubbed",),
    )
    claims = extract_claims_from_artifact(art, "actor:employer")
    assert any(c.actor_id == "system:anchorum" for c in claims)
