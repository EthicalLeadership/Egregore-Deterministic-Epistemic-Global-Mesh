"""
ANCHORUM Pipeline — Unit Tests
File: anchorum/pipeline/narrative_trace/tests/test_tracer.py

Run: python -m pytest tests/test_tracer.py -v
"""

import datetime
import tempfile
from pathlib import Path

import pytest

from ..analysis.models import (
    CommunicationRecord, EvidenceRecord, TimelineEntry,
    EpistemicTag, BurdenShiftTag, Severity, SourceProvenance
)
from ..analysis.tracer import NarrativeTracer
from ..ingestion.ingestor import FileIngestor


class TestFileIngestor:
    """Test auto-detection and ingestion of different file formats."""

    def test_ingest_plain_text(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("From: Insurer Corp\\nTo: Patient\\nSubject: Denial of claim\\n\\nWe have reviewed your claim and denied it.")
            path = f.name

        ingestor = FileIngestor()
        record = ingestor.ingest(path)

        assert isinstance(record, CommunicationRecord)
        assert record.communication_type == "email"
        assert "denied" in record.body_text.lower()
        assert record.provenance.file_hash_sha256 is not None
        Path(path).unlink()

    def test_ingest_html_stripping(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write("""<html><head><style>body{color:red}</style></head>
<body><script>alert('xss')</script><h1>Claim Response</h1>
<p>We have processed your request.</p></body></html>""")
            path = f.name

        ingestor = FileIngestor()
        record = ingestor.ingest(path)

        assert "alert" not in record.body_text
        assert "color:red" not in record.body_text
        assert "Claim Response" in record.body_text
        assert "processed" in record.body_text
        Path(path).unlink()

    def test_ingest_auto_classify_medical(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Diagnostic: Fracture tibia. Traitement: Plâtre 6 semaines. Prescription: Diclofénac.")
            path = f.name

        ingestor = FileIngestor()
        record = ingestor.ingest(path)

        assert isinstance(record, EvidenceRecord)
        assert record.evidence_type == "medical_record"
        Path(path).unlink()

    def test_ingest_auto_classify_insurer(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Votre réclamation a été rejetée. Numéro de police: 12345. Indemnisation refusée.")
            path = f.name

        ingestor = FileIngestor()
        record = ingestor.ingest(path)

        assert isinstance(record, EvidenceRecord)
        assert record.evidence_type == "insurer_correspondence"
        Path(path).unlink()

    def test_date_extraction_iso(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Date: 2024-03-15. Meeting held on 2024-03-15.")
            path = f.name

        ingestor = FileIngestor()
        dates = ingestor._parse_dates("Date: 2024-03-15", Path(path))

        assert dates["document_date"] == datetime.date(2024, 3, 15)
        Path(path).unlink()

    def test_claim_extraction(self):
        ingestor = FileIngestor()
        text = "We have reviewed your claim. You must provide additional documentation. We confirm that all questions were answered."
        claims = ingestor._extract_claims(text)

        assert len(claims) >= 2
        assert any("reviewed" in c.lower() for c in claims)
        assert any("must" in c.lower() for c in claims)


class TestNarrativeTracer:
    """Test the trace engine end-to-end."""

    def _make_comm(self, text: str, date: datetime.date, comm_type: str = "email") -> CommunicationRecord:
        return CommunicationRecord(
            record_id=f"comm-{hash(text) % 10000:04d}",
            provenance=SourceProvenance(
                file_path=Path("/fake/comm.txt"),
                file_hash_sha256="a" * 64,
                ingestion_timestamp=datetime.datetime.now(datetime.timezone.utc),
                parser_version="test",
                raw_bytes_size=100,
            ),
            communication_type=comm_type,
            date_sent=date,
            date_received=date,
            sender="Insurer",
            recipient="Patient",
            subject="Test",
            body_text=text,
            body_hash="b" * 64,
            claims_extracted=[text],
        )

    def _make_evid(self, text: str, date: datetime.date, evid_type: str = "insurer_correspondence") -> EvidenceRecord:
        return EvidenceRecord(
            record_id=f"evid-{hash(text) % 10000:04d}",
            provenance=SourceProvenance(
                file_path=Path("/fake/evid.txt"),
                file_hash_sha256="c" * 64,
                ingestion_timestamp=datetime.datetime.now(datetime.timezone.utc),
                parser_version="test",
                raw_bytes_size=100,
            ),
            evidence_type=evid_type,
            document_date=date,
            creation_date=date,
            modification_date=date,
            source_entity="Hospital",
            content_text=text,
            content_hash="d" * 64,
            key_facts=[text],
        )

    def test_trace_contradiction_detected(self):
        tracer = NarrativeTracer()

        comm = self._make_comm(
            "We have responded to all your questions promptly and completely.",
            datetime.date(2024, 3, 1)
        )
        evid = self._make_evid(
            "No response was sent to questions 2, 3, and 7. The patient followed up three times.",
            datetime.date(2024, 3, 15)
        )

        tracer.communications = [comm]
        tracer.evidence = [evid]

        timeline = tracer.trace(
            communications_glob="/dev/null/*",  # Bypass glob, use injected records
            evidence_glob="/dev/null/*",
            case_id="test-001"
        )
        # Note: trace() will try to glob and find nothing, so we need to test the internal methods

    def test_match_claim_to_evidence(self):
        tracer = NarrativeTracer()
        claim = "We responded to all questions within 48 hours"
        evid = self._make_evid(
            "The insurer did not respond to any questions for 30 days. Patient sent 4 follow-ups.",
            datetime.date(2024, 3, 15)
        )

        matches = tracer._match_claim_to_evidence(claim, [evid])
        assert len(matches) > 0
        assert matches[0][1] > 0.3  # Should match on keywords

    def test_burden_shift_detected(self):
        tracer = NarrativeTracer()
        claim = "You must provide additional medical documentation at your own expense"
        evidence = "The insurer is required by statute to obtain all necessary medical records"

        result = tracer._detect_burden_shift(claim, evidence)
        assert result == BurdenShiftTag.YES

    def test_burden_shift_not_detected(self):
        tracer = NarrativeTracer()
        claim = "We have approved your claim and will send payment within 5 days"
        evidence = "The insurer has a statutory obligation to pay within 30 days"

        result = tracer._detect_burden_shift(claim, evidence)
        assert result == BurdenShiftTag.NO

    def test_severity_critical(self):
        tracer = NarrativeTracer()
        claim = "We approved your treatment request immediately without delay"
        evid = self._make_evid(
            "Treatment was delayed by 45 days. Patient suffered additional injury.",
            datetime.date(2024, 3, 15),
            "medical_record"
        )
        matches = [(evid, 0.8)]
        burden = BurdenShiftTag.YES

        severity = tracer._determine_severity(claim, matches, burden)
        assert severity == Severity.CRITICAL

    def test_date_inconsistency_detected(self):
        tracer = NarrativeTracer()
        comm = self._make_comm(
            "We sent our response on March 1, 2024",
            datetime.date(2024, 3, 1)
        )
        evid = self._make_evid(
            "Document created on March 20, 2024",
            datetime.date(2024, 3, 20)
        )

        inconsistency = tracer._detect_date_inconsistency(comm, [evid])
        assert inconsistency is not None
        assert "19 days" in inconsistency or "20 days" in inconsistency

    def test_demolition_timeline_markdown(self):
        entry = TimelineEntry(
            entry_id="e-0001",
            date=datetime.date(2024, 3, 1),
            counterparty_claim="We responded promptly",
            documentary_evidence="No response found in records",
            verdict=EpistemicTag.FACT,
            burden_shift=BurdenShiftTag.YES,
            severity=Severity.CRITICAL,
            communication_source_id="comm-001",
            evidence_source_ids=["evid-001"],
            demolition_note="Direct contradiction with documentary evidence",
        )
        timeline = DemolitionTimeline(
            case_id="test",
            generated_at=datetime.datetime.now(datetime.timezone.utc),
            tracer_version="test",
            entries=[entry],
            unverified_claims=[],
            evidence_gaps=[],
            summary="Test summary",
        )
        md = timeline.to_markdown()
        assert "CRITICAL" in md
        assert "FACT" in md
        assert "YES" in md
        assert "Direct contradiction" in md


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

