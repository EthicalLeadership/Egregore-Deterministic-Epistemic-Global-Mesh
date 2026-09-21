# ANCHORUM Narrative Trace & Demolition Pipeline

## Self-Describing Contract

This pipeline interprets files automatically. You do not need to tell it what format each file is.

### What the tool sees and how it interprets it

| File Extension | Detection Method | Interpretation |
|---------------|------------------|-----------------|
| \`.eml\`, \`.msg\` | Magic bytes + MIME | RFC822 email → \`CommunicationRecord\` (email) |
| \`.pdf\` | Magic bytes (\`%PDF\`) | PDF text extraction → \`EvidenceRecord\` or \`CommunicationRecord\` (auto-classified) |
| \`.docx\`, \`.doc\` | ZIP magic + MIME | Word document text → auto-classified |
| \`.txt\`, \`.md\` | Extension | Plain text → auto-classified |
| \`.html\`, \`.htm\` | Extension | HTML stripped (scripts/styles removed) → auto-classified |
| \`.json\` | Extension | Structured metadata → attached as metadata |
| \`.csv\`, \`.xlsx\` | Extension | Tabular data → attached as metadata |

### Auto-Classification Rules

The tool reads the first 5KB of text and scores it against keyword dictionaries:

**Communication types:** email, letter, meeting_note, phone_log, form
**Evidence types:** medical_record, insurer_correspondence, employer_file, legal_statute, expert_opinion

If classification is ambiguous, the tool defaults to \`evidence/employer_file\` (safer for legal proceedings).

### Date Extraction

Multiple patterns are scanned:
- ISO: \`2024-03-15\`
- French: \`15/03/2024\`, \`15 mars 2024\`
- Email headers: \`Date: Mon, 15 Mar 2024 14:30:00 +0000\`
- File system timestamps as fallback

### Exact Commands

\`\`\`bash
# 1. Install dependencies (if not already present)
pip install pdfminer.six python-docx

# 2. Run the pipeline
python -m anchorum.pipeline.narrative_trace \\
    --communications "/path/to/molson/emails/*.eml" \\
    --evidence "/path/to/molson/medical/*.pdf" \\
    --output "/path/to/molson/demolition_timeline.md" \\
    --case-id molson-2026

# 3. Mixed formats (recursive)
python -m anchorum.pipeline.narrative_trace \\
    --communications "/path/to/molson/comms/**/*" \\
    --evidence "/path/to/molson/evidence/**/*" \\
    --output "/path/to/molson/demolition_timeline.md" \\
    --case-id molson-2026 \\
    --verbose

# 4. Run tests
python -m pytest anchorum/pipeline/narrative_trace/tests/test_tracer.py -v
\`\`\`

### Output Format

The pipeline produces a tribunal-ready markdown report with:
- Chronological timeline of contradictions
- Epistemic grounding per claim: \`[FACT | DERIVED | MODEL | UNCERTAIN]\`
- Burden shift detection: \`[YES | NO | IMPLICIT]\`
- Severity classification: \`[CRITICAL | HIGH | MEDIUM | LOW]\`
- Unverified claims (no documentary match)
- Evidence gaps (documentary evidence with no counterparty claim)

### Architecture

\`\`\`
anchorum/pipeline/narrative_trace/
├── __init__.py          # Package manifest with self-describing contracts
├── __main__.py          # CLI entry point
├── analysis/
│   ├── __init__.py
│   ├── models.py        # Immutable dataclasses: CommunicationRecord, EvidenceRecord, TimelineEntry, DemolitionTimeline
│   └── tracer.py        # NarrativeTracer engine: cross-reference, contradiction detection, burden shift analysis
├── ingestion/
│   ├── __init__.py
│   └── ingestor.py      # FileIngestor: auto-detect format, auto-classify, extract dates, extract claims
└── tests/
    ├── __init__.py
    └── test_tracer.py   # Unit tests: ingestion, classification, matching, burden shift, severity, timeline generation
\`\`\`

### Epistemic Grounding Rules

- **FACT**: Directly observable from primary source (e.g., email header date vs. evidence creation date)
- **DERIVED**: Logical inference from multiple FACTs (e.g., pattern of 3 unanswered questions + insurer claim of "all answered")
- **MODEL**: Pattern match against known adversarial tactics (e.g., rhetorical burden shift framing)
- **UNCERTAIN**: Insufficient evidence — flagged explicitly, never treated as falsity

