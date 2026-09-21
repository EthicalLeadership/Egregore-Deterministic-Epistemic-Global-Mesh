# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project is pre-1.0; minor versions may include breaking changes.
Commit SHAs are abbreviated to 7 characters.

## [v0.6.0-phase1] — 2026-08-03

Phase 7 of the factory program plus operator tooling and backup hardening.

### Added

- Factory replay determinism harness (`scripts/factory_replay.py`): runs a
  workload twice at temperature 0 with fixed seed and byte-compares outputs
  plus volatile-stripped telemetry traces. MOLSON-2026 verified
  DETERMINISTIC. (`7ba57bc`)
- `case_report` factory mode: 5-station legal narrative with per-mode output
  contracts (`required_output_fields_by_mode` in `config/factory_policy.json`).
  (`7ba57bc`)
- Weekly factory report (`scripts/factory_weekly_report.py`): FAIL families
  per `policy_hash`, synthetic-vs-real BLOCKED rate, critic p95 trend.
  (`7ba57bc`)
- GBNF grammar critic, repair tier, and citation gating. (`250a7a7`)
- Pre-registered week-end decision table (cutoff commit `250a7a7`).
  (`55cecb4`)
- Factory operator tab in the desktop app — zero-HTTP, filesystem-sourced.
  (`8891c31`)
- Daily USB cold-sync backup service + timer (03:30), rsync exit-code 23
  tolerated. (`64e0669`)

## [v0.5.0] — 2026-08-02

Phase 6 — VRAM residency: llama.cpp GGUF fleet becomes the hot inference path.

### Added

- `GgufBackend` (`src/egregore/infrastructure/gguf_backend.py`): full GPU
  offload, lazy per-model load. `my-coder-ft` (7B Q4_K_M) for standard
  stations, `qwen-1.5b` for the QC critic (~230 ms verdicts). (`eaa7d31`)
- Per-station VRAM pre-flight: shortfall raises `VramInsufficientError` →
  BLOCKED with `vram_insufficient` in milliseconds. (`eaa7d31`)
- Heavy-pass swap: escalation unloads hot residents, loads the HF 8-bit
  model, runs, unloads, restores residents; serialized by a lock. (`eaa7d31`)
- Fine-tune GGUF salvage: `my_coder_ft_fixed-Q4_K_M.gguf` re-converted with
  `byte_fallback: false`; legacy corrupt GGUFs quarantined. (`edf2169`)

### Changed

- Heavy escalation defaults to the hot GGUF 7B; HF swap is opt-in.
  (`0a2529d`)
- Heavy pass differentiated by parameters (`policy.escalation`). (`4035565`)
- `factory.inference` telemetry events record the serving backend. (`04b83a6`)

### Fixed

- Heavy-pass swap VRAM leak; added `CoderBackend.close()`. (`288eaaa`)

## [v0.4.0] — 2026-08-02

Factory measurement and governance: telemetry, fail-closed QC, policy file.

### Added

- Phase 1 — canonical-JSONL factory telemetry recorder
  (`src/egregore/factory/telemetry.py`) and histogram bucketer
  (`scripts/factory_histogram.py`). Hook points: `factory.envelope.in`,
  `factory.station`, `factory.inference`, `factory.run.outcome`. (`841abdb`)
- Phase 2 — fail-closed QC gate (Station 5, `src/egregore/factory/qc_gate.py`):
  deterministic checks plus semantic critic, rework budget 2, one heavy
  escalation pass, then BLOCKED. BLOCKED runs ship nothing. (`655b857`)
- Phase 3 — `config/factory_policy.json` as governance contract: malformed
  policy = BLOCKED, env > file > defaults precedence, `policy_hash` embedded
  in telemetry, hot reload by mtime. (`33b6aba`)

## [v0.3.0] — 2026-08-02

Factory S1 intake, egregore-native router, ANCHORUM site and desktop app.

### Added

- Factory Station 1 intake and the egregore-native model router. (`4aae3ae`)
- ANCHORUM forensic site and the native Tkinter desktop client
  (`anchorum_desktop.py`). (`4aae3ae`)

## [v0.2.0] — 2026-07-30

Self-study library enrichment and deployment audit trail.

### Added

- Griffiths QM recommended arc extension and local source-paper archive.
  (`97e11e9`)
- Cleaned post-incident review for the dashboard stack deployment.
  (`311ba04`)

### Changed

- Pending workspace changes applied. (`ccfe546`)

## [v0.1.0] — 2026-07-21

Initial public release: Egregore deterministic-epistemic global mesh.

### Added

- `.zarc` provenance writer: canonical JSON, SHA-256 hash chain, Ed25519
  signatures, `verify_chain()` integrity checking.
- CBI-0 governance enforcement (M1–M4) with fail-closed halts.
- Governance adapters (`DagSigner`, `AnchorumBridge`,
  `LitigationHoldTrigger`), powertrain logic, NATS JetStream telemetry.
- FastAPI core, npm-managed gateway, deployment pipeline with architecture
  purity / CBI-0 / Gate 5 gates. (`2239969`)

[v0.6.0-phase1]: https://github.com/EthicalLeadership/Egregore-Deterministic-Epistemic-Global-Mesh/compare/250a7a7...64e0669
[v0.5.0]: https://github.com/EthicalLeadership/Egregore-Deterministic-Epistemic-Global-Mesh/compare/33b6aba...edf2169
[v0.4.0]: https://github.com/EthicalLeadership/Egregore-Deterministic-Epistemic-Global-Mesh/compare/4aae3ae...33b6aba
[v0.3.0]: https://github.com/EthicalLeadership/Egregore-Deterministic-Epistemic-Global-Mesh/compare/311ba04...4aae3ae
[v0.2.0]: https://github.com/EthicalLeadership/Egregore-Deterministic-Epistemic-Global-Mesh/compare/2239969...311ba04
[v0.1.0]: https://github.com/EthicalLeadership/Egregore-Deterministic-Epistemic-Global-Mesh/commit/2239969
