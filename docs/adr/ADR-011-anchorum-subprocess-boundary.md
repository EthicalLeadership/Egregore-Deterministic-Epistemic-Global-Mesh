# ADR-011: ANCHORUM External-Tool Subprocess Boundary

## Status
Accepted — Commercial Hardening Sprint, Day 2

## Context
ANCHORUM integrates several third-party forensic tools that carry license
obligations incompatible with the proprietary Egregore core. Examples include:

- **qpdf** (Apache 2.0) for PDF liberation.
- **Tesseract** (Apache 2.0) for OCR.
- **ExifTool** (Artistic/GPL) for metadata extraction.
- **steghide** (GPL) for steganography detection.
- **zsteg** (MIT) for PNG/BMP stego detection.

The Egregore license boundary requires that GPL/AGPL code never be imported or
linked into the proprietary runtime. At the same time, ANCHORUM must be safe
against command-injection and path-traversal bugs when it invokes external
binaries.

## Decision
All external tool invocations are centralized in a single audited runner:
`anchorum.forensic.core.shell._run_external`.

The runner enforces the following invariants:

1. **Executable allow-list.** Only explicitly registered binaries may be invoked.
   The allow-list is a constant set inside `shell.py`.
2. **Absolute-path resolution.** `shutil.which` resolves the binary to an absolute
   path before `subprocess.run` is called, eliminating partial-path warnings.
3. **No shell interpretation.** Arguments are passed as a list; no user input is
   interpolated into a shell string.
4. **Localized security suppressions.** `noqa: S603` and bandit skips for the
   subprocess module are applied only at the runner; call sites do not silence
   security rules.
5. **Call-site timeout and capture.** Every external call supplies an explicit
   timeout and captures stdout/stderr to a `.zarc` audit record, satisfying
   CBI-0 M3/M4.

Tools register themselves in the capability manifest
(`anchorum.forensic.core.manifest`) with name, version, plane, description,
dependencies, and license so that a license scan can be reproduced from code.

## Consequences

- **License isolation is verifiable.** A grep for GPL-only imports in
  `src/anchorum` returns no results; only subprocess calls are present.
- **Subprocess security review is concentrated.** Future external tools must be
  added to the allow-list and documented in `ANCHORUM-CREDITS.md`.
- **Call sites are simpler.** They pass a list of arguments and handle tool-
   specific return-code semantics, not process spawning.
- **Test coverage improves.** The runner can be mocked or exercised with a fake
  port in unit tests without relying on the external binary being installed.

## Related

- `src/anchorum/forensic/core/shell.py`
- `src/anchorum/forensic/core/manifest.py`
- `ANCHORUM-CREDITS.md`
- ADR-010: ANCHORUM Integrity Gate
- ADR-003: CBI-0 Governance Checkpoints
