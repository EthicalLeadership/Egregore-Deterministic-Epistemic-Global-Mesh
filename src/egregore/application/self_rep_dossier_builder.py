"""High-level builder that produces the SelfRep self-representation dossier."""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from egregore.domain.self_rep_dossier.actor_classifier import ActorRegistry
from egregore.domain.self_rep_dossier.claim_extractor import (
    extract_claims_from_artifact,
)
from egregore.domain.self_rep_dossier.contradiction_detector import (
    detect_contradictions_and_corroborations,
)
from egregore.domain.self_rep_dossier.dossier_models import (
    Artifact,
    Claim,
    Dossier,
    EvidenceGap,
    Thread,
)
from egregore.domain.self_rep_dossier.evidence_parser import parse_self_rep_evidence
from egregore.domain.self_rep_dossier.procedure_rule_mapper import (
    map_dossier_to_procedure_rules,
)
from egregore.domain.self_rep_dossier.thread_builder import ThreadBuilder
from egregore.interface.document_extraction_port import DocumentTextExtractorPort
from egregore.interface.domain_data_ports import (
    DossierDataSource,
    RuleRegistrySource,
)


class SelfRepDossierBuilder:
    """Build a self-representation dossier from ANCHORUM-extracted SelfRep evidence."""

    def __init__(
        self,
        extracted_path: Path | str | None = None,
        report_path: Path | str | None = None,
        dossier_root: Path | str | None = None,
        dossier_source: DossierDataSource | None = None,
        document_extractor: DocumentTextExtractorPort | None = None,
        rule_source: RuleRegistrySource | None = None,
    ) -> None:
        self.extracted_path = extracted_path
        self.report_path = report_path
        self.dossier_root = dossier_root
        self.dossier_source = dossier_source
        self.document_extractor = document_extractor
        self.rule_source = rule_source

    def build(self, case_id: str = "CASE-00000-00") -> Dossier:
        """Run the full pipeline and return a Dossier."""
        extracted_path = str(self.extracted_path) if self.extracted_path else None
        report_path = str(self.report_path) if self.report_path else None
        dossier_root = (
            str(self.dossier_root)
            if self.dossier_root
            else os.environ.get("DOSSIER_ROOT", "/opt/egregore/dossier")
        )
        if self.dossier_source is None:
            raise ValueError(
                "SelfRepDossierBuilder requires a dossier_source (e.g. FileSystemDossierAdapter). "
                "Pass it at construction to keep the application layer free of infrastructure imports."
            )
        if self.document_extractor is None:
            raise ValueError(
                "SelfRepDossierBuilder requires a document_extractor (e.g. DocumentTextExtractorAdapter). "
                "Pass it at construction to keep the application layer free of infrastructure imports."
            )
        dossier_source = self.dossier_source
        artifacts, report = parse_self_rep_evidence(
            extracted_path=extracted_path,
            report_path=report_path,
            dossier_root=dossier_root,
            dossier_source=dossier_source,
            document_extractor=self.document_extractor,
        )

        registry = ActorRegistry()

        # Classify every artifact.
        artifact_actor_map: dict[str, str] = {}
        for artifact in artifacts:
            actor_id = registry.classify_artifact(artifact)
            artifact_actor_map[artifact.artifact_id] = actor_id

        # Build email threads.
        thread_builder = ThreadBuilder(registry)
        for artifact in artifacts:
            if artifact.modality == "email":
                thread_builder.add_artifact(artifact)
        threads = thread_builder.build_threads()

        # Extract claims.
        actor_roles = {a.actor_id: a.party_role for a in registry.all_actors()}
        claims: list[Claim] = []
        for artifact in artifacts:
            actor_id = artifact_actor_map.get(artifact.artifact_id, "actor:unknown")
            party_role = actor_roles.get(actor_id, "unknown")
            claims.extend(extract_claims_from_artifact(artifact, actor_id, party_role))

        # Detect contradictions and corroborations.
        contradictions, corroborations = detect_contradictions_and_corroborations(
            claims
        )

        # Identify evidence gaps.
        gaps = self._identify_gaps(
            artifacts, claims, registry, threads, artifact_actor_map
        )

        # Sort timeline by timestamp.
        timeline = tuple(
            sorted(
                [c for c in claims if c.timestamp],
                key=lambda c: c.timestamp or datetime.min.replace(tzinfo=UTC),
            )
        )

        # Build preliminary dossier to map claims to Quebec procedure/evidence rules.
        preliminary = Dossier(
            case_id=case_id,
            generated_at=datetime.fromtimestamp(time.time_ns() / 1e9, tz=UTC),
            actors=registry.all_actors(),
            artifacts=tuple(artifacts),
            claims=tuple(claims),
            threads=threads,
            contradictions=contradictions,
            corroborations=corroborations,
            gaps=tuple(gaps),
            timeline=timeline,
            source_paths={
                "extracted_jsonl": str(self.extracted_path or "default"),
                "anchorum_report": str(self.report_path or "default"),
                "dossier_root": str(self.dossier_root or "default"),
            },
        )
        if self.rule_source is None:
            raise ValueError(
                "SelfRepDossierBuilder requires a rule_source (e.g. FileSystemRuleRegistryAdapter). "
                "Pass it at construction to keep the application layer free of infrastructure imports."
            )
        procedure_rules = tuple(
            map_dossier_to_procedure_rules(preliminary, self.rule_source)
        )

        return Dossier(
            case_id=case_id,
            generated_at=datetime.fromtimestamp(time.time_ns() / 1e9, tz=UTC),
            actors=registry.all_actors(),
            artifacts=tuple(artifacts),
            claims=tuple(claims),
            threads=threads,
            contradictions=contradictions,
            corroborations=corroborations,
            gaps=tuple(gaps),
            timeline=timeline,
            applicable_procedure_rules=procedure_rules,
            source_paths={
                "extracted_jsonl": str(self.extracted_path or "default"),
                "anchorum_report": str(self.report_path or "default"),
                "dossier_root": str(self.dossier_root or "default"),
            },
        )

    def _identify_gaps(  # noqa: C901
        self,
        artifacts: list[Artifact],
        claims: list[Claim],
        registry: ActorRegistry,
        threads: tuple[Thread, ...],
        artifact_actor_map: dict[str, str],
    ) -> list[EvidenceGap]:
        """Find evidence gaps that weaken the dossier."""
        gaps: list[EvidenceGap] = []
        gap_counter = 0

        # 1. High-stakes institutional claims with only one source.
        #    Only refusals, obligations, or explicit assertions from opposing
        #    parties that would benefit from independent corroboration.
        for claim in claims:
            if claim.party_role in ("claimant", "system", "unknown", ""):
                continue
            if claim.modality in ("system", "image", "recording"):
                continue
            if claim.claim_type not in {"refusal", "obligation"}:
                continue
            if claim.confidence < 0.7:
                continue
            if len(claim.text) < 40:
                continue
            gap_counter += 1
            gaps.append(
                EvidenceGap(
                    gap_id=f"gap:{gap_counter:04d}",
                    description=f"Institutional {claim.claim_type} has only one source: '{claim.text[:120]}...'",
                    related_actor_ids=(claim.actor_id,),
                    related_claim_ids=(claim.claim_id,),
                    severity="medium",
                    gap_type="missing_corroboration",
                )
            )

        # 2. Artifacts with extraction errors.
        for artifact in artifacts:
            if artifact.extraction_errors:
                gap_counter += 1
                gaps.append(
                    EvidenceGap(
                        gap_id=f"gap:{gap_counter:04d}",
                        description=f"Extraction errors for {artifact.filename}: {'; '.join(artifact.extraction_errors)}",
                        related_actor_ids=(
                            artifact_actor_map.get(
                                artifact.artifact_id, "actor:unknown"
                            ),
                        ),
                        related_claim_ids=(),
                        severity="high",
                        gap_type="missing_document",
                    )
                )

        # 3. High-severity anomalies (metadata scrubbed) flagged by ANCHORUM.
        #    Medium findings (after-hours/weekend creation) are summarized in one
        #    aggregate gap to avoid noise.
        scrubbed_count = 0
        scrubbed_by_actor: dict[str, int] = defaultdict(int)
        medium_count = 0
        for artifact in artifacts:
            high_anomalies = [a for a in artifact.anomalies if "high_findings" in a]
            if high_anomalies:
                scrubbed_count += 1
                actor_id = artifact_actor_map.get(artifact.artifact_id, "actor:unknown")
                scrubbed_by_actor[actor_id] += 1
                gap_counter += 1
                gaps.append(
                    EvidenceGap(
                        gap_id=f"gap:{gap_counter:04d}",
                        description=f"Metadata possibly scrubbed in {artifact.filename}: {'; '.join(high_anomalies[:2])}",
                        related_actor_ids=(actor_id,),
                        related_claim_ids=(),
                        severity="high",
                        gap_type="anomaly",
                    )
                )
            if any("medium_findings" in a for a in artifact.anomalies):
                medium_count += 1

        if medium_count:
            gap_counter += 1
            gaps.append(
                EvidenceGap(
                    gap_id=f"gap:{gap_counter:04d}",
                    description=f"{medium_count} artifacts have medium-severity ANCHORUM findings (after-hours/weekend creation/timezone inconsistency or embedded concealment).",
                    related_actor_ids=tuple(scrubbed_by_actor.keys())
                    or ("actor:unknown",),
                    related_claim_ids=(),
                    severity="low",
                    gap_type="anomaly",
                )
            )

        # 4. Threads with only one party represented.
        for thread in threads:
            if len(thread.participants) == 1:
                gap_counter += 1
                gaps.append(
                    EvidenceGap(
                        gap_id=f"gap:{gap_counter:04d}",
                        description=f"Email thread '{thread.subject[:80]}' has only one participant; missing replies may exist.",
                        related_actor_ids=thread.participants,
                        related_claim_ids=(),
                        severity="medium",
                        gap_type="missing_response",
                    )
                )

        return gaps


# Need artifact_actor_map accessible inside _identify_gaps; we passed it implicitly
# by recomputing from registry. Recompute mapping here.
# Actually the method above uses artifact_actor_map which is local to build().
# Fix: pass it explicitly.
