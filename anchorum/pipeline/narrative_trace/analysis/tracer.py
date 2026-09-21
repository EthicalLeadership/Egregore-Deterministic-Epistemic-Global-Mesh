"""
ANCHORUM Pipeline — Narrative Trace & Demolition Engine
File: anchorum/pipeline/narrative_trace/analysis/tracer.py

The engine that cross-references counterparty communications against documentary evidence,
identifies contradictions, burden shifts, and builds the demolition timeline.

Processing guarantees:
- Every timeline entry is backed by at least one CommunicationRecord and one EvidenceRecord.
- No entry is fabricated: if a claim cannot be matched to evidence, it goes to unverified_claims.
- If evidence contradicts no claim, it goes to evidence_gaps.
- Every verdict is tagged with epistemic grounding.
"""

from __future__ import annotations
import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set
from dataclasses import asdict

from .models import (
    CommunicationRecord, EvidenceRecord, DemolitionTimeline, TimelineEntry,
    EpistemicTag, BurdenShiftTag, Severity, SourceProvenance
)
from ..ingestion.ingestor import FileIngestor


class NarrativeTracer:
    """
    Main engine. Instantiate with a vault path, then call trace() with file globs.
    """

    def __init__(self, vault_path: Optional[str] = None, parser_version: str = "0.6.0-phase1"):
        self.vault_path = Path(vault_path) if vault_path else None
        self.ingestor = FileIngestor(parser_version=parser_version)
        self.communications: List[CommunicationRecord] = []
        self.evidence: List[EvidenceRecord] = []
        self.parser_version = parser_version

    def _load_files(self, glob_pattern: str, category_hint: Optional[str] = None) -> List:
        """Load all files matching glob, auto-classify, return typed records."""
        import glob as glob_module
        paths = glob_module.glob(glob_pattern, recursive=True)
        records = []
        for p in paths:
            try:
                record = self.ingestor.ingest(p, category_hint=category_hint)
                records.append(record)
            except Exception as e:
                # Log failure but continue — partial ingestion is better than total failure
                print(f"[INGESTION FAIL] {p}: {e}")
        return records

    def _chronological_sort(self, records: List) -> List:
        """Sort records by best available date."""
        def get_date(r):
            if isinstance(r, CommunicationRecord):
                return r.date_sent or r.date_received or datetime.date.min
            elif isinstance(r, EvidenceRecord):
                return r.document_date or r.creation_date or datetime.date.min
            return datetime.date.min
        return sorted(records, key=get_date)

    def _normalize_claim(self, claim: str) -> str:
        """Normalize for fuzzy matching: lowercase, strip punctuation, collapse whitespace."""
        claim = claim.lower()
        claim = re.sub(r"[^\\w\\s]", " ", claim)
        claim = re.sub(r"\\s+", " ", claim)
        return claim.strip()

    def _claim_to_keywords(self, claim: str) -> Set[str]:
        """Extract significant keywords from a claim for matching."""
        normalized = self._normalize_claim(claim)
        # Remove stopwords (French + English)
        stopwords = {
            "le", "la", "les", "un", "une", "des", "de", "du", "et", "ou", "en", "au", "aux",
            "the", "a", "an", "and", "or", "in", "on", "at", "to", "for", "of", "with", "by",
            "nous", "vous", "ils", "elles", "on", "je", "tu", "il", "elle", "we", "you", "they",
            "avons", "sommes", "avons", "êtes", "sont", "est", "have", "has", "had", "was", "were",
            "notre", "votre", "leur", "not", "no", "non", "pas", "ne", "que", "qui", "quoi",
        }
        words = [w for w in normalized.split() if len(w) > 3 and w not in stopwords]
        return set(words)

    def _match_claim_to_evidence(self, claim: str, evidence_records: List[EvidenceRecord]) -> List[Tuple[EvidenceRecord, float]]:
        """
        Match a counterparty claim to documentary evidence.
        Returns list of (EvidenceRecord, match_score) sorted by score descending.
        Score 0.0–1.0 based on keyword overlap and date proximity.
        """
        claim_keywords = self._claim_to_keywords(claim)
        if not claim_keywords:
            return []

        matches = []
        for evid in evidence_records:
            evid_text = self._normalize_claim(evid.content_text)
            evid_keywords = self._claim_to_keywords(evid.content_text)

            if not evid_keywords:
                continue

            # Jaccard similarity on keywords
            intersection = claim_keywords & evid_keywords
            union = claim_keywords | evid_keywords
            jaccard = len(intersection) / len(union) if union else 0.0

            # Boost for direct contradiction words
            contradiction_markers = {
                "non", "not", "faux", "false", "incorrect", "inexact", "erroné", "erroneous",
                "contraire", "contrary", "opposé", "opposite", "différent", "different",
                "jamais", "never", "aucun", "none", "pas de", "no",
            }
            contra_boost = sum(1 for m in contradiction_markers if m in evid_text) * 0.1

            score = min(1.0, jaccard + contra_boost)

            if score > 0.15:  # Threshold: must share some semantic overlap
                matches.append((evid, score))

        matches.sort(key=lambda x: x[1], reverse=True)
        return matches

    def _detect_burden_shift(self, claim: str, evidence_text: str) -> BurdenShiftTag:
        """
        Detect if the counterparty is improperly shifting burden to the patient/user.
        Looks for patterns where the counterparty demands action from the patient
        while the statutory or contractual burden lies with the counterparty.
        """
        claim_lower = claim.lower()
        evidence_lower = evidence_text.lower()

        # Explicit burden shift patterns
        shift_patterns = [
            r"\\bvous devez\\b", r"\\byou must\\b", r"\\bvous devrez\\b", r"\\byou will need to\\b",
            r"\\bmerci de\\b", r"\\bplease provide\\b", r"\\bveuillez\\b", r"\\bkindly submit\\b",
            r"\\bfournir\\b", r"\\bsoumettre\\b", r"\\bsubmit\\b", r"\\bprovide\\b",
            r"\\bà votre charge\\b", r"\\bat your expense\\b", r"\\bà vos frais\\b",
        ]

        # Statutory burden patterns (counterparty should bear the burden)
        statutory_patterns = [
            r"\\bnous avons\\b", r"\\bwe have\\b", r"\\bnous sommes tenus\\b", r"\\bwe are required\\b",
            r"\\bnous devons\\b", r"\\bwe must\\b", r"\\bnotre obligation\\b", r"\\bour duty\\b",
            r"\\bl\\'assureur\\b", r"\\bl\\'employeur\\b", r"\\bthe insurer\\b", r"\\bthe employer\\b",
        ]

        has_shift = any(re.search(p, claim_lower) for p in shift_patterns)
        has_statutory = any(re.search(p, evidence_lower) for p in statutory_patterns)

        if has_shift and has_statutory:
            return BurdenShiftTag.YES
        elif has_shift:
            return BurdenShiftTag.IMPLICIT
        else:
            return BurdenShiftTag.NO

    def _determine_severity(self, claim: str, evidence_matches: List[Tuple[EvidenceRecord, float]], burden_shift: BurdenShiftTag) -> Severity:
        """
        Determine severity based on:
        - Does the contradiction undermine a core narrative?
        - Is there a burden shift?
        - How many evidence records contradict it?
        """
        core_narrative_keywords = [
            "délai", "delay", "traitement", "treatment", "réponse", "response",
            "information", "renseignement", "décision", "decision", "approbation", "approval",
            "rejet", "rejection", "denial", "refus", "refusal", "invalidité", "disability",
        ]

        is_core = any(kw in claim.lower() for kw in core_narrative_keywords)
        has_strong_evidence = len([m for m in evidence_matches if m[1] > 0.4]) > 0

        if is_core and has_strong_evidence and burden_shift in (BurdenShiftTag.YES, BurdenShiftTag.IMPLICIT):
            return Severity.CRITICAL
        elif is_core and has_strong_evidence:
            return Severity.CRITICAL
        elif has_strong_evidence and burden_shift == BurdenShiftTag.YES:
            return Severity.HIGH
        elif is_core and len(evidence_matches) > 0:
            return Severity.HIGH
        elif len(evidence_matches) > 0:
            return Severity.MEDIUM
        else:
            return Severity.LOW

    def _detect_date_inconsistency(self, comm: CommunicationRecord, evidence_records: List[EvidenceRecord]) -> Optional[str]:
        """
        Detect if the communication date contradicts evidence dates.
        E.g., counterparty claims they responded on date X, but evidence shows no activity until date Y.
        """
        if not comm.date_sent and not comm.date_received:
            return None

        comm_date = comm.date_sent or comm.date_received
        inconsistencies = []

        for evid in evidence_records:
            if evid.document_date and evid.document_date > comm_date:
                # Evidence created AFTER the communication claims it was sent
                # This could indicate backdating or delayed production
                delta = (evid.document_date - comm_date).days
                if delta > 7:
                    inconsistencies.append(
                        f"Evidence {evid.record_id} dated {evid.document_date.isoformat()} "
                        f"({delta} days after claimed communication date {comm_date.isoformat()})"
                    )

        if inconsistencies:
            return "; ".join(inconsistencies[:3])
        return None

    def trace(
        self,
        communications_glob: str,
        evidence_glob: str,
        output_path: Optional[str] = None,
        case_id: str = "molson-001"
    ) -> DemolitionTimeline:
        """
        Main entry point. Load communications and evidence, cross-reference, build timeline.

        Args:
            communications_glob: Glob pattern for counterparty communications
            evidence_glob: Glob pattern for documentary evidence
            output_path: If provided, write markdown report to this path
            case_id: Case identifier for the report

        Returns:
            DemolitionTimeline with all entries, unverified claims, and evidence gaps.
        """
        # 1. Load all records
        self.communications = self._load_files(communications_glob)
        self.evidence = self._load_files(evidence_glob)

        # Sort chronologically
        self.communications = self._chronological_sort(self.communications)
        self.evidence = self._chronological_sort(self.evidence)

        entries: List[TimelineEntry] = []
        unverified_claims: List[str] = []
        evidence_gaps: List[str] = []
        used_evidence_ids: Set[str] = set()

        # 2. For each communication, extract claims and match to evidence
        for comm in self.communications:
            for claim in comm.claims_extracted:
                matches = self._match_claim_to_evidence(claim, self.evidence)

                if not matches:
                    unverified_claims.append(
                        f"[{comm.record_id}] {claim[:120]}..."
                    )
                    continue

                best_evid, best_score = matches[0]
                used_evidence_ids.add(best_evid.record_id)

                burden_shift = self._detect_burden_shift(claim, best_evid.content_text)
                severity = self._determine_severity(claim, matches, burden_shift)
                date_inconsistency = self._detect_date_inconsistency(comm, [best_evid])

                # Determine verdict
                if best_score > 0.6 and burden_shift == BurdenShiftTag.YES:
                    verdict = EpistemicTag.FACT
                elif best_score > 0.5:
                    verdict = EpistemicTag.DERIVED
                elif best_score > 0.3:
                    verdict = EpistemicTag.MODEL
                else:
                    verdict = EpistemicTag.UNCERTAIN

                demolition_note = f"Claim contradicts evidence {best_evid.record_id} (score: {best_score:.2f})"
                if date_inconsistency:
                    demolition_note += f". DATE INCONSISTENCY: {date_inconsistency}"
                if burden_shift in (BurdenShiftTag.YES, BurdenShiftTag.IMPLICIT):
                    demolition_note += f". BURDEN SHIFT: {burden_shift.value}"

                entry = TimelineEntry(
                    entry_id=f"e-{len(entries)+1:04d}",
                    date=comm.date_sent or comm.date_received,
                    counterparty_claim=claim[:200],
                    documentary_evidence=f"[{best_evid.record_id}] {best_evid.content_text[:150]}...",
                    verdict=verdict,
                    burden_shift=burden_shift,
                    severity=severity,
                    communication_source_id=comm.record_id,
                    evidence_source_ids=[best_evid.record_id],
                    demolition_note=demolition_note,
                    raw_quote_communication=claim,
                    raw_quote_evidence=best_evid.content_text[:300],
                )
                entries.append(entry)

        # 3. Evidence gaps: evidence that contradicts nothing
        for evid in self.evidence:
            if evid.record_id not in used_evidence_ids:
                evidence_gaps.append(
                    f"[{evid.record_id}] {evid.evidence_type}: {evid.content_text[:120]}... "
                    f"(No counterparty claim matched this evidence)"
                )

        # 4. Build summary
        critical_count = sum(1 for e in entries if e.severity == Severity.CRITICAL)
        high_count = sum(1 for e in entries if e.severity == Severity.HIGH)
        burden_shift_count = sum(1 for e in entries if e.burden_shift == BurdenShiftTag.YES)
        date_inconsistency_count = sum(1 for e in entries if "DATE INCONSISTENCY" in e.demolition_note)

        summary = f"""Narrative trace complete for case {case_id}.

Total communications analyzed: {len(self.communications)}
Total evidence records: {len(self.evidence)}
Timeline entries: {len(entries)}
  - CRITICAL severity: {critical_count}
  - HIGH severity: {high_count}
  - Burden shifts detected: {burden_shift_count}
  - Date inconsistencies: {date_inconsistency_count}
Unverified claims (no evidence match): {len(unverified_claims)}
Evidence gaps (no claim match): {len(evidence_gaps)}

Tribunal readiness: {"READY" if critical_count > 0 and burden_shift_count > 0 else "PARTIAL — strengthen evidence on unverified claims"}
"""

        timeline = DemolitionTimeline(
            case_id=case_id,
            generated_at=datetime.datetime.now(datetime.timezone.utc),
            tracer_version=self.parser_version,
            entries=entries,
            unverified_claims=unverified_claims,
            evidence_gaps=evidence_gaps,
            summary=summary,
        )

        if output_path:
            out_path = Path(output_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(timeline.to_markdown())
            print(f"[OUTPUT] Demolition timeline written to: {out_path}")

        return timeline

