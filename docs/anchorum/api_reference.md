# ANCHORUM API Reference

This document describes the public surface of the ANCHORUM forensic document
intelligence engine. All functions below are deterministic where noted and emit
CBI-0 `.zarc` audit events.

## Top-level imports

```python
from anchorum.forensic.core.document import (
    liberate,                    # PDF Liberation
    detect_obstruction,          # PDF Obstruction Detector
    recover_revisions,           # Office Deep Revision Recovery
    analyze_ocr,                 # OCR Confidence Pipeline
    detect_steganography,        # Steganography Statistical Detector
    fuse_timelines,              # Cross-Document Timeline Fusion
)
```

---

## Plane 1 — Document Ingest & Classification

### `liberate(input_pdf, password, output_dir, case_id, operator)`

Remove PDF encryption and owner restrictions, producing a clean derivative.

| Argument | Type | Description |
|----------|------|-------------|
| `input_pdf` | `Path` | Locked PDF (must be read-only). |
| `password` | `str \| None` | Owner password, if known. |
| `output_dir` | `Path` | Derivative output directory. |
| `case_id` | `str` | Case identifier for provenance. |
| `operator` | `str` | Operator username. |

**Returns:** `dict` with `original_hash`, `clean_hash`, `clean_path`,
`restrictions_before`, `restrictions_after`, `encryption_removed`,
`manifest_path`.

**Raises:**
- `FileNotFoundError` — input PDF missing.
- `PermissionError` — original is writable (CBI-0 M1).
- `ValueError` — bad password or unsupported encryption.
- `ValueError` — input exceeds `MAX_INPUT_BYTES`.

### `detect_obstruction(source, case_id, operator)`

Detect bad-faith disclosure patterns in a PDF.

| Argument | Type | Description |
|----------|------|-------------|
| `source` | `Path \| bytes \| BinaryIO` | PDF to analyse. |
| `case_id` | `str` | Case identifier. |
| `operator` | `str` | Operator username. |

**Returns:** `EventReference` pointing to the `.zarc` report.

**Signals:** heavy restrictions, JavaScript, embedded files, form/XFA
obfuscation, rasterization, low DPI, incremental encryption abuse, suspicious
producer.

### `PdfPharosEngine.classify(path)`

Classify a PDF by type, redaction markers, and scanned-page heuristics.

**Returns:** `DocumentVerdict` with `file_type`, `is_redacted`, `is_scanned`,
`page_count`, `classification_confidence`, `details`.

### `MetadataExtractor.extract(path)`

Extract forensic metadata from PDF Info dictionaries.

**Returns:** `MetadataPlane` with `producer`, `creator`, `created`, `modified`,
`title`, `author`, `encrypted`, `exiftool_available`, `raw`.

### `SignaturePharos.inspect(path)`

Detect PDF signature fields.

**Returns:** `SignatureVerdict` with `signed_count`, `unsigned_count`,
`has_expired`, `details`.

### `HiddenLayerDetector.inspect(path)`

Detect optional content layers, embedded files, JavaScript actions, and
annotations.

**Returns:** `HiddenLayerVerdict`.

### `IntegrityAttestor.attest(path)`

Compute a SHA-256 manifest of an input document.

**Returns:** `IntegrityAttestation`.

---

## Plane 2 — Content Extraction & Statistical Analysis

### `recover_revisions(source, case_id, operator)`

Recover hidden and visible track changes, comments, and previous-version
metadata from OOXML documents.

**Returns:** `EventReference` to the `.zarc` report.

**Data classes:** `DocumentRevisionReport`, `RevisionEntry`, `CommentEntry`,
`VersionMetadata`.

### `analyze_ocr(source, language="eng", case_id, operator, fallback_engines=None)`

Run OCR with per-word confidence and fallback engine chain.

**Returns:** `EventReference` to the `.zarc` report.

**Data classes:** `OcrWord`, `OcrPageResult`, `OcrReport`, `TesseractCliEngine`.

### `detect_steganography(source, case_id, operator, external_tools=None)`

Statistical steganography detection in images.

**Returns:** `EventReference` to the `.zarc` report.

**Data classes:** `LsbAnalysis`, `EntropyAnalysis`, `StegoReport`, `ToolResult`.

**External tool ports:** `StegoToolPort`, `SteghideTool`, `ZstegTool`.

---

## Plane 3 — Cross-Document Analysis

### `fuse_timelines(*sources, case_id, operator)`

Collect timestamped events from files, Office revisions, PDF metadata, email
headers, and raw events into a single chronology.

**Sources:**
- `FileMetadataSource(path)`
- `OfficeRevisionSource(revisions, comments)`
- `JsonFileRevisionSource(json_path)`
- `PdfMetadataSource(metadata)`
- `EmailHeaderSource(raw_headers)`
- `RawEventSource(events)`

**Returns:** `EventReference` to the `.zarc` report.

**Data classes:** `TimelineEvent`, `TimelineAnomaly`, `FusedTimeline`.

---

## Plane 4 — Fused Manifest

### `compile_fused_manifest(case_id, operator, *, liberated=None, obstruction=None, ocr=None, stego=None, revisions=None, timeline=None, integrity=None)`

Fuse outputs from Planes 1–3 into a single ANCHORUM Document Intelligence
Record (ADIR).

**Returns:** `FusedManifest`.

---

## Governance & Audit

### `emit_zarc_event(event_type, case_id, operator, payload)`

Emit a provenance event and return a deterministic event ID.

### `anchorum.forensic.core.manifest.register_tool(...)`

Register an external capability in the ANCHORUM capability manifest.

---

## Resource Limits

All public engines enforce a default maximum input size of
`anchorum.forensic.core.validation.MAX_INPUT_BYTES` (512 MiB). Callers may rely
on a `ValueError` when the limit is exceeded.
