"""Unit tests for SelfRep actor classifier."""

from __future__ import annotations

from datetime import UTC, datetime

from egregore.domain.self_rep_dossier.actor_classifier import ActorRegistry
from egregore.domain.self_rep_dossier.dossier_models import Artifact


def _artifact(from_addr: str | None = None, filename: str = "test.txt") -> Artifact:
    metadata = {}
    if from_addr:
        metadata["from_addr"] = from_addr
    return Artifact(
        artifact_id="a1",
        source_path=f"/tmp/{filename}",  # noqa: S108
        filename=filename,
        container_type="email" if from_addr else "txt",
        modality="email" if from_addr else "text",
        timestamp=datetime(2025, 5, 1, tzinfo=UTC),
        content_text="",
        metadata=metadata,
    )


def test_claimant_email_consolidated():
    registry = ActorRegistry()
    art = _artifact(from_addr="claimant@example.net")
    actor_id = registry.classify_artifact(art)
    assert actor_id == "actor:claimant:self_represented"


def test_employer_email_classified():
    registry = ActorRegistry()
    art = _artifact(from_addr="hr@example.com")
    actor_id = registry.classify_artifact(art)
    assert actor_id == "actor:email:hr_example_com"
    actor = registry.actors[actor_id]
    assert actor.party_role == "employer"


def test_filename_heuristic_employer():
    registry = ActorRegistry()
    art = _artifact(filename="Acme_HR_memo.pdf")
    actor_id = registry.classify_artifact(art)
    assert registry.actors[actor_id].party_role == "employer"
