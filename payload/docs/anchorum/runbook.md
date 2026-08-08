# ANCHORUM Operator Runbook

Quick reference for running ANCHORUM forensic engines safely in production and
debugging common issues.

## Pre-requisites

- Python 3.11+
- Editable install: `pip install -e .` from the repository root.
- External binaries (optional unless noted):
  - `qpdf` — PDF Liberation
  - `tesseract` — OCR Confidence Pipeline
  - `steghide` — steganography detection (GPL, subprocess only)
  - `zsteg` — PNG/BMP stego detection (Ruby gem, MIT)
  - `exiftool` — metadata extraction fallback

Verify discovery:

```python
from anchorum.forensic.core.manifest import registered_tools
print([t["name"] for t in registered_tools()])
```

## Typical workflows

### Unlock a restricted PDF

```python
from pathlib import Path
from anchorum.forensic.core.document import liberate

manifest = liberate(
    input_pdf=Path("locked.pdf"),
    password="owner123",
    output_dir=Path("/case/derivatives"),
    case_id="CASE-2026-001",
    operator="alice",
)
print(manifest["clean_path"])
```

### Detect obstruction

```python
from anchorum.forensic.core.document import detect_obstruction

ref = detect_obstruction(
    source=Path("disputed.pdf"),
    case_id="CASE-2026-001",
    operator="alice",
)
print(ref.audit_path)
```

### OCR a scanned page

```python
from anchorum.forensic.core.document import analyze_ocr

ref = analyze_ocr(
    source=Path("scan.png"),
    language="eng",
    case_id="CASE-2026-001",
    operator="alice",
)
```

### Recover Word track changes

```python
from anchorum.forensic.core.document import recover_revisions

ref = recover_revisions(
    source=Path("contract.docx"),
    case_id="CASE-2026-001",
    operator="alice",
)
```

### Fuse a timeline

```python
from anchorum.forensic.core import fuse_timelines
from anchorum.forensic.core.timeline_fusion import FileMetadataSource, PdfMetadataSource

ref = fuse_timelines(
    FileMetadataSource("contract.docx"),
    PdfMetadataSource({"creation_date": "D:20260101000000Z"}),
    case_id="CASE-2026-001",
    operator="alice",
)
```

## Audit trail

Every engine writes a JSON event under:

```text
/tmp/anchorum_zarc/<case_id>/<event_id>.json
```

The path uses `tempfile.gettempdir()`; set `TMPDIR` to redirect it if required.
Events are also appended to an in-memory log for testing:

```python
from anchorum.forensic.core.provenance import emitted_events, clear_events
```

## Common issues

### `PermissionError: Original file must be read-only`

CBI-0 M1 requires the original PDF to be read-only before liberation. Make the
file immutable or copy it with `chmod 444`.

### `FileNotFoundError: Required external executable not found`

The requested binary is missing or not on `PATH`. Install the tool and ensure it
is discoverable. ANCHORUM does not fall back to a different binary name.

### `ValueError: input size exceeds maximum allowed`

The input exceeds `MAX_INPUT_BYTES` (default 512 MiB). For legitimate large
evidence files, split or preprocess the file before ingestion.

### OCR returns `FAIL` confidence

- Verify `tesseract` is installed and the requested language pack is available.
- Check that the input image is not heavily compressed or degraded.
- Review the `.zarc` report for per-word confidence and anomaly flags.

### Steganography tools report "not installed"

`steghide` and `zsteg` are optional. The statistical detector (LSB chi-square
and entropy) still runs without them. Install them if the case requires
external-tool correlation.

## Security checklist

- [ ] Confirm GPL tools (`steghide`, `exiftool`) are invoked only via subprocess.
- [ ] Confirm no `fitz` / PyMuPDF imports exist in `src/anchorum`.
- [ ] Verify `anchorum.forensic.core.shell._ALLOWED_BINARIES` contains only
      reviewed binaries.
- [ ] Review `/tmp/anchorum_zarc` permissions — events contain case metadata.

## Health checks

Run the ANCHORUM test suite:

```bash
pytest tests/anchorum -q
```

Run architecture enforcement tests:

```bash
pytest tests/test_arch_enforcement.py tests/test_architecture_policy_intent.py -q
```

Run linting:

```bash
ruff check src/anchorum tests/anchorum
mypy src/anchorum
bandit -r src/anchorum
```
