# 06 — Infrastructure Adapters

Status: `implemented & tested` (with noted defects in PostgreSQL adapter)

The infrastructure layer (Plane 2) implements the ports defined in `interface/`. It contains persistence, telemetry, messaging, sediment archive, and local-LLM adapters. It may import `domain`, `interface`, and `kernel` but not `application`.

## Zarc journal

Source: `src/egregore/infrastructure/zarc_journal.py`.

`ZarcJournal` is the canonical event-sourced persistence adapter. It implements:

- `ITransactionalPersistence`
- `IIdempotencyStore`
- `ICaseStore`
- `ICommitJournal`

On startup it replays the `.zarc` chain and rebuilds in-memory read models:

- success results by fingerprint
- case views by `(organization_id, case_id)`
- snapshots/events/outbox by `execution_id`
- terminal fingerprints

Key property: the `.zarc` file is the database. Read models are deterministic projections.

See [02 — Zarc Provenance](02_zarc_provenance.md) for format details.

## SQLite persistence

Source: `src/egregore/infrastructure/persistence/sqlite_dossier_adapter.py`.

`SQLiteTransactionalPersistence` is an alternative T2 backend:

- One SQLite `.db` per node; WAL mode; foreign keys.
- Companion `.zarc` file written via `kernel.Provenance`.
- Tables: `case_versions`, `dossier_commits`.
- Fail-closed on `timestamp_ns=None`.
- Idempotent: checks for existing `execution_id` before insert.
- Emits `.zarc` outside the SQLite transaction; a `zarc_emitted` flag handles crash recovery.

This adapter demonstrates that Plane 2 persistence can vary while still feeding the same audit chain.

## PostgreSQL persistence

Source: `src/egregore/infrastructure/adapters/postgresql_persistence.py`.

Status: `implemented with defects`.

`PostgreSQLPersistence` is a Plane 2 PostgreSQL adapter. It currently has several issues:

1. **Duplicate method** — `query_audit_log` is defined twice (lines ~53 and ~259).
2. **Conflicting imports** — `CommitResult` is imported from both `egregore.interface.semantics_ports` and `egregore.domain.models.dossier`.
3. **Policy violation** — uses `json.loads`/`json.dumps` directly instead of `shared/canonical.py`.
4. **Wrong interface signature** — `commit_generate_t2(self, dossier: Dossier) -> CommitResult` does not match `ITransactionalPersistence.commit_generate_t2(...)`.
5. **Missing required fields** — the adapter does not accept the full command/artifacts signature expected by the executor.

Because of these issues, the adapter fails architecture-policy tests and cannot be used as a drop-in backend for `CorePlaneGenerateDossierExecutor`.

## Zarc provenance sink

Source: `src/egregore/infrastructure/zarc_provenance_sink.py`.

`ZarcProvenanceSink` adapts domain `ProvenanceEvent` objects to `kernel.Provenance.append()`. It implements:

- `IProvenanceSink`
- `IProvenanceVerifier`

This decouples domain code from the kernel writer while preserving the `.zarc` format.

## Sediment archive

Source: `src/egregore/infrastructure/sediment_archive.py`.

`SedimentArchive` stores dead agencies as immutable fossils in stratified layers:

- `FossilRegister` — immutable dead-agency record.
- `Stratum` — epoch-layered fossil collection.
- `fossilize(agency)` — creates a fossil and assigns a `sediment_id`.
- `query(...)` — retrieve fossils by species, biome, lobe, or epoch.
- `compress_stratum(epoch)` — deduplicate fossils within a sealed stratum.

Note: the current implementation uses `datetime.utcnow()` and `time.time()` for epoch/timestamp, which conflicts with the core determinism rule. This is acceptable only because sediment archiving is currently a Plane 2 side effect outside the critical dossier path.

## Local model catalog

Source: `src/egregore/infrastructure/local_model_catalog.py`.

`LocalModelCatalog` provides deterministic on-disk GGUF model routing:

- Vertical-aware selection (`legal`, `operations`, `dt1`, `default`).
- Policy-version compatibility filtering.
- Speed-tier preference (`fast`, `balanced`, `quality`).
- Stable model ordering by `model_id`.
- SHA256 hash pinning; fail-closed on mismatch.
- Manifest loading via canonical JSON.

`build_default_fast_catalog(models_root)` provides opinionated defaults.

## Local LLM adapter

Source: `src/egregore/infrastructure/local_llm_adapter.py`.

### KimiK2 loader adapter

Source: `src/egregore/infrastructure/kimik2_loader_adapter.py`.

`Kimik2LoaderAdapter` implements the `IKimik2Loader` port using Hugging Face `transformers` and `torch`. It:

- Validates model shards and `model.safetensors.index.json`.
- Skips real loading in `KIMIK2_TEST_MODE=1` or when shards are dummy placeholders.
- Requires `temperature=0.0` for deterministic generation.
- Raises `Kimik2LoaderError` on any failure.


`LocalLlmAdapter` is a lazy `llama-cpp-python` wrapper:

- Imported lazily so CPU-only tests do not require the dependency.
- Computes model, prompt, and output SHA256 hashes.
- `generate()` returns text + hashes.

The adapter computes hashes locally rather than importing `shared/canonical.py`, keeping the Plane 2 layer dependency matrix clean.

## Telemetry

### Collector

Source: `src/egregore/infrastructure/telemetry/telemetry_collector.py`.

`Phase0TelemetryCollector` emits canonical telemetry envelopes:

- Gates: `cpu`, `memory`, `storage`, `network`, `gpu`, `interconnect`.
- `derive_deterministic_timestamp_ns()` — derives `timestamp_ns` from stable inputs, no wall-clock.
- `TelemetryEnvelope.to_payload_bytes()` — canonical JSON payload.

### Pulse adapter

Source: `src/egregore/cortex/pulse_adapter.py`.

`PulseAdapter` publishes a single JSON pulse to `obs.pulse.<node_id>`. It takes all telemetry getters as injected callables.

### Phase-0 agents

- `infrastructure/telemetry/phase0_pulse_agent.py` — gate-based telemetry loop.
- `cortex/phase0_pulse_agent.py` — minimal pulse telemetry agent.

Both are transport-agnostic and accept an injected `PublisherLike`.

## NATS / JetStream broker

Source: `src/egregore/bus/nats_broker.py`.

`bootstrap_jetstream()` idempotently creates JetStream streams:

- Accepts a `JetStreamLike` protocol object.
- Detects already-exists conflicts and ignores them.
- No direct `nats-py` imports, keeping tests dependency-free.

## Bootstrap / facades

Source: `src/egregore/infrastructure/bootstrap.py`.

`get_dossier_facade()` uses `importlib` to provide the `DossierServiceFacade` to HTTP layer while avoiding a static cross-layer import node.

## Invariants

| Invariant | Enforcement |
|---|---|
| Plane 2 may import domain/interface/kernel | Allowed by architecture tests. |
| No `application/` imports in infrastructure | `tests/test_arch_enforcement.py`. |
| Deterministic timestamps in core adapters | `ZarcJournal` and `SQLiteTransactionalPersistence` raise on missing `timestamp_ns`. |
| Canonical serialization single-sourced | PostgreSQL adapter currently violates this. |

## Known defects

- PostgreSQL adapter has duplicate methods, conflicting imports, direct `json` usage, and wrong interface signature.
- Sediment archive uses wall-clock time.

## Tests

- `tests/test_sqlite_dossier_adapter.py`
- `tests/test_local_model_catalog.py`
- `tests/test_local_llm_cse_integration.py`
- `tests/test_local_vertical_inference.py`
- `tests/test_nats_broker.py`
- `tests/test_pulse_adapter.py`
- `tests/test_architecture_policy_intent.py`
