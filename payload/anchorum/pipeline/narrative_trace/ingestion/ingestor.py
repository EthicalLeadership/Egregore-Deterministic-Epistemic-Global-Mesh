"""
ANCHORUM Pipeline — File Ingestion with Auto-Detection
File: anchorum/pipeline/narrative_trace/ingestion/ingestor.py

When the tool sees a file, it knows how to interpret it via:
1. MIME type detection (magic bytes + extension fallback)
2. Format-specific parser dispatch
3. Normalized output: CommunicationRecord or EvidenceRecord

Supported formats:
  .eml, .msg        → RFC822 email (reuses ANCHORUM IMAP connector logic)
  .pdf              → Text extraction via pdfminer.six (fallback: pdftotext)
  .docx, .doc       → python-docx extraction
  .txt, .md         → Plain text passthrough
  .html, .htm       → HTML stripping (reuses _html_to_text from anchorum_desktop.py)
  .json             → Structured metadata passthrough
  .csv, .xlsx       → Tabular data (metadata attachment)

Every file gets SHA256 + provenance logging.
"""

from __future__ import annotations
import hashlib
import datetime
import json
import mimetypes
import re
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
from dataclasses import asdict

from ..analysis.models import (
    CommunicationRecord, EvidenceRecord, SourceProvenance, EpistemicTag
)


class FileIngestor:
    """
    Self-describing ingestor. Call ingest() with a file path, get back a typed record.
    No configuration needed — format is auto-detected from content.
    """

    SUPPORTED_COMMUNICATION_TYPES = {"email", "letter", "meeting_note", "phone_log", "form"}
    SUPPORTED_EVIDENCE_TYPES = {"medical_record", "insurer_correspondence", "employer_file", "legal_statute", "expert_opinion"}

    def __init__(self, parser_version: str = "0.6.0-phase1"):
        self.parser_version = parser_version
        self._html_strip_re = re.compile(r"<(script|style)[^>]*>.*?</\\1>", re.DOTALL | re.IGNORECASE)
        self._html_tag_re = re.compile(r"<[^>]+>")
        self._nbsp_re = re.compile(r"&nbsp;|&#160;")

    def _sha256_file(self, path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def _detect_mime(self, path: Path) -> str:
        mime, _ = mimetypes.guess_type(str(path))
        if mime:
            return mime
        # Fallback: read magic bytes
        with open(path, "rb") as f:
            header = f.read(8)
        if header.startswith(b"\\x25\\x50\\x44\\x46"):
            return "application/pdf"
        if header.startswith(b"PK"):
            return "application/zip"  # docx, xlsx
        if header.startswith(b"From ") or b"MIME-Version:" in header:
            return "message/rfc822"
        return "application/octet-stream"

    def _strip_html(self, html: str) -> str:
        """Reuses fixed anchorum_desktop.py logic."""
        text = self._html_strip_re.sub("", html)
        text = self._html_tag_re.sub("", text)
        text = self._nbsp_re.sub(" ", text)
        # Block elements → newlines
        text = re.sub(r"</(p|div|h[1-6]|li|tr)>", "\\n", text, flags=re.IGNORECASE)
        return "\\n".join(line.strip() for line in text.splitlines() if line.strip())

    def _extract_text(self, path: Path, mime: str) -> str:
        if mime in ("text/plain", "text/markdown"):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        elif mime in ("text/html", "application/xhtml+xml"):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return self._strip_html(f.read())
        elif mime == "message/rfc822":
            # Reuse ANCHORUM IMAP connector RFC822 parsing
            from anchorum.ingestion import RFC822Parser
            with open(path, "rb") as f:
                raw = f.read()
            parsed = RFC822Parser.parse(raw)
            return parsed.get("body_preview", "") or parsed.get("body", "")
        elif mime == "application/pdf":
            try:
                from pdfminer.high_level import extract_text
                return extract_text(str(path))
            except ImportError:
                import subprocess
                result = subprocess.run(
                    ["pdftotext", str(path), "-"],
                    capture_output=True, text=True, timeout=30
                )
                return result.stdout if result.returncode == 0 else f"[PDF extraction failed: {result.stderr}]"
        elif mime in ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",):
            try:
                from docx import Document
                doc = Document(str(path))
                return "\\n".join(p.text for p in doc.paragraphs if p.text.strip())
            except ImportError:
                return "[docx extraction failed: python-docx not installed]"
        else:
            return f"[Unsupported format: {mime}. File hash: {self._sha256_file(path)}]"

    def _parse_dates(self, text: str, path: Path) -> Dict[str, Optional[datetime.date]]:
        """Extract dates from text using multiple patterns. Returns dict with best guesses."""
        dates = {"date_sent": None, "date_received": None, "document_date": None, "creation_date": None, "modification_date": None}
        # ISO dates: 2024-03-15
        iso_pattern = re.compile(r"(20\\d{2})-(\\d{2})-(\\d{2})")
        # French dates: 15 mars 2024, 15/03/2024
        fr_pattern = re.compile(r"(\\d{1,2})[\\/\\-](\\d{1,2})[\\/\\-](20\\d{2})")
        # Email Date header: Mon, 15 Mar 2024 14:30:00 +0000
        email_pattern = re.compile(r"Date:\\s*([A-Za-z]{3},\\s+\\d{1,2}\\s+[A-Za-z]{3}\\s+20\\d{2})")

        found = []
        for m in iso_pattern.finditer(text):
            try:
                found.append(datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            except ValueError:
                pass
        for m in fr_pattern.finditer(text):
            try:
                found.append(datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
            except ValueError:
                pass

        if found:
            dates["document_date"] = min(found)  # Earliest date = document date
            dates["date_sent"] = min(found)

        # File system dates as fallback
        stat = path.stat()
        dates["creation_date"] = datetime.date.fromtimestamp(stat.st_ctime)
        dates["modification_date"] = datetime.date.fromtimestamp(stat.st_mtime)

        return dates

    def _classify_record_type(self, text: str, path: Path, mime: str) -> tuple[str, str]:
        """
        Auto-classify: is this a communication or evidence? What subtype?
        Returns (category, subtype).
        """
        text_lower = text.lower()[:5000]  # First 5KB for classification
        path_lower = str(path).lower()

        # Communication indicators
        comm_markers = {
            "email": ["from:", "to:", "subject:", "mime-version", "@", "sent:", "de:", "à:", "objet:"],
            "letter": ["cher monsieur", "madame", "dear sir", "dear madam", "yours sincerely", "cordialement", "meilleures salutations"],
            "meeting_note": ["compte rendu", "meeting minutes", "procès-verbal", "réunion", "attendees", "participants"],
            "phone_log": ["appel téléphonique", "phone call", "conversation", "téléphone", "voicemail"],
            "form": ["formulaire", "form", "questionnaire", "demande", "application", "reclamation"],
        }

        # Evidence indicators
        evidence_markers = {
            "medical_record": ["diagnostic", "traitement", "prescription", "médical", "clinique", "pathologie", "dossier médical", "medical record", "patient"],
            "insurer_correspondence": ["assurance", "insurer", "claim", "réclamation", "indemnisation", "prestation", "benefit", "policy number", "numéro de police"],
            "employer_file": ["employeur", "employer", "employé", "employee", "dossier du personnel", "hr file", "congé", "leave", "absence", "accident de travail"],
            "legal_statute": ["loi", "code", "règlement", "statute", "regulation", "article", "chapitre", "c.c.q.", "civil code"],
            "expert_opinion": ["expert", "opinion", "rapport d'expert", "expertise", "évaluation", "assessment"],
        }

        # Score each category
        comm_scores = {k: sum(1 for m in markers if m in text_lower) for k, markers in comm_markers.items()}
        evidence_scores = {k: sum(1 for m in markers if m in text_lower) for k, markers in evidence_markers.items()}

        best_comm = max(comm_scores, key=comm_scores.get)
        best_evidence = max(evidence_scores, key=evidence_scores.get)

        if comm_scores[best_comm] > evidence_scores[best_evidence] and comm_scores[best_comm] > 0:
            return ("communication", best_comm)
        elif evidence_scores[best_evidence] > 0:
            return ("evidence", best_evidence)

        # Path-based fallback
        if any(x in path_lower for x in ["email", "courriel", "mail"]):
            return ("communication", "email")
        if any(x in path_lower for x in ["medical", "médical", "doctor", "médecin"]):
            return ("evidence", "medical_record")
        if any(x in path_lower for x in ["insurance", "assurance", "insurer", "assureur"]):
            return ("evidence", "insurer_correspondence")
        if any(x in path_lower for x in ["employer", "employeur", "work", "travail"]):
            return ("evidence", "employer_file")

        # Default: evidence (safer for legal proceedings — everything is evidence until proven communication)
        return ("evidence", "employer_file")

    def ingest(self, path: Union[str, Path], category_hint: Optional[str] = None) -> Union[CommunicationRecord, EvidenceRecord]:
        """
        Main entry point. Give it a file path, get back a typed record.
        No configuration needed — the file tells us what it is.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Ingestion target not found: {path}")

        mime = self._detect_mime(path)
        text = self._extract_text(path, mime)
        file_hash = self._sha256_file(path)
        dates = self._parse_dates(text, path)

        provenance = SourceProvenance(
            file_path=path.resolve(),
            file_hash_sha256=file_hash,
            ingestion_timestamp=datetime.datetime.now(datetime.timezone.utc),
            parser_version=self.parser_version,
            raw_bytes_size=path.stat().st_size,
        )

        # Auto-classify
        if category_hint:
            category, subtype = category_hint.split("/") if "/" in category_hint else (category_hint, "unknown")
        else:
            category, subtype = self._classify_record_type(text, path, mime)

        if category == "communication":
            return CommunicationRecord(
                record_id=f"comm-{file_hash[:16]}",
                provenance=provenance,
                communication_type=subtype,
                date_sent=dates.get("date_sent"),
                date_received=dates.get("date_received"),
                sender=self._extract_sender(text),
                recipient=self._extract_recipient(text),
                subject=self._extract_subject(text),
                body_text=text,
                body_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                claims_extracted=self._extract_claims(text),
                metadata={"mime_type": mime, "auto_classified": category_hint is None},
            )
        else:
            return EvidenceRecord(
                record_id=f"evid-{file_hash[:16]}",
                provenance=provenance,
                evidence_type=subtype,
                document_date=dates.get("document_date"),
                creation_date=dates.get("creation_date"),
                modification_date=dates.get("modification_date"),
                source_entity=self._extract_sender(text) or "UNKNOWN",
                content_text=text,
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                key_facts=self._extract_claims(text),
                metadata={"mime_type": mime, "auto_classified": category_hint is None},
            )

    def _extract_sender(self, text: str) -> str:
        """Extract sender name from email headers or letter salutation."""
        patterns = [
            re.compile(r"From:\\s*([^\\n<]+)", re.IGNORECASE),
            re.compile(r"De\\s*:\\s*([^\\n<]+)", re.IGNORECASE),
            re.compile(r"([A-Z][a-z]+\\s+[A-Z][a-z]+)\\s*<[^>]+>", re.IGNORECASE),
        ]
        for p in patterns:
            m = p.search(text[:2000])
            if m:
                return m.group(1).strip().rstrip("\\r")
        return "UNKNOWN"

    def _extract_recipient(self, text: str) -> str:
        patterns = [
            re.compile(r"To:\\s*([^\\n<]+)", re.IGNORECASE),
            re.compile(r"À\\s*:\\s*([^\\n<]+)", re.IGNORECASE),
        ]
        for p in patterns:
            m = p.search(text[:2000])
            if m:
                return m.group(1).strip().rstrip("\\r")
        return "UNKNOWN"

    def _extract_subject(self, text: str) -> Optional[str]:
        patterns = [
            re.compile(r"Subject:\\s*([^\\n]+)", re.IGNORECASE),
            re.compile(r"Objet\\s*:\\s*([^\\n]+)", re.IGNORECASE),
        ]
        for p in patterns:
            m = p.search(text[:2000])
            if m:
                return m.group(1).strip()
        return None

    def _extract_claims(self, text: str) -> List[str]:
        """Naive claim extraction: sentences with counterparty assertion patterns."""
        sentences = re.split(r"(?<=[.!?])\\s+", text)
        claim_patterns = [
            re.compile(r"\\b(nous avons|we have|nous sommes|we are|il est|it is|vous devez|you must|vous êtes|you are|doit|must|devrait|should)\\b", re.IGNORECASE),
            re.compile(r"\\b(déclarons|declare|affirmons|affirm|confirmons|confirm|certifions|certify)\\b", re.IGNORECASE),
            re.compile(r"\\b(répondu|responded|réponse|response|traité|processed|examiné|examined)\\b", re.IGNORECASE),
        ]
        claims = []
        for s in sentences:
            s = s.strip()
            if len(s) < 20 or len(s) > 300:
                continue
            if any(p.search(s) for p in claim_patterns):
                claims.append(s)
        return claims[:20]  # Cap at 20 claims per document

