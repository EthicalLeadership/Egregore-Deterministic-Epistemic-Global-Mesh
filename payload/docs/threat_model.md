# Reproducible Fusion Engine — Adversarial Threat Model

## Threat Agents
- **External Adversary:** Can submit arbitrary evidence streams to the API.
- **Insider (low privilege):** Can modify configuration files or replace model weights.
- **Compromised Data Source:** A legitimate stream source emits corrupted or crafted data.
- **Network Attacker:** Can intercept or replay API traffic.

## Attack Vectors & Mitigations

### V1. Stream Injection (Forged Evidence)
- **Attack:** Adversary submits a synthetic stream with crafted metadata (timestamp, tags, confidence) to influence the report.
- **Mitigation:**
  - Every stream MUST carry a valid Ed25519 signature from a registered source.
  - Unsigned streams have their authority weight halved (configurable).
  - `security.py` verifies signatures before stream acceptance.
  - Streams that fail signature verification are rejected with HTTP 422.

### V2. Timestamp Manipulation
- **Attack:** Backdating evidence to bypass decay or future-dating to appear more recent.
- **Mitigation:**
  - Enforce monotonic clock: timestamps must be ≤ current time.
  - Future timestamps are rejected (HTTP 422).
  - Timestamps outside a configurable skew window (default 5 min) are flagged in the decision log.
  - Backdated evidence still decays unless tagged foundational, which requires a separate FEA signature.

### V3. Metadata Corruption (relevance_tags, confidence)
- **Attack:** Adversary manipulates tags or confidence scores to misroute or over-weight evidence.
- **Mitigation:**
  - All metadata fields are included in the signed payload; modification invalidates signature.
  - Confidence scores from AI sources are capped at 0.95 and down-weighted relative to structured sources.
  - Relevance tags are validated against a controlled vocabulary; unknown tags result in "untrusted" labeling and minimal weight.

### V4. Foundational Tag Forgery
- **Attack:** Adversary marks false evidence as foundational (immune to decay).
- **Mitigation:**
  - The `foundational` flag requires a separate Ed25519 signature from a Foundational Evidence Authority (FEA).
  - FEA public keys are configured in `rfe_config.yaml`.
  - Without a valid FEA signature, a stream claiming foundational is downgraded to standard and logged as a security event.

### V5. Config Substitution (Decay/Arbitration Manipulation)
- **Attack:** Adversary replaces `rfe_config.yaml` to alter decay rates, weights, or thresholds.
- **Mitigation:**
  - The config file is versioned and its SHA-256 hash is stored in the decision log.
  - On startup, the RFE verifies the config hash against the `RFE_CONFIG_HASH` environment variable; mismatch aborts.
  - The `/api/v1/rfe/config` endpoint serves the active config with its hash for external audit.

### V6. Arbitration Tampering
- **Attack:** Adversary exploits arbitration logic to force a favorable resolution or synthetic dispute.
- **Mitigation:**
  - The arbitration algorithm (see `arbitration_spec.md`) is deterministic and version-pinned.
  - Any algorithm change requires a version bump and is recorded in the decision log.
  - The red-team harness includes tests that feed crafted conflicting streams and verify the arbitration outcome matches the spec.

### V7. .zarc Chain Poisoning
- **Attack:** Adversary inserts invalid entries into the .zarc journal, breaking the hash chain.
- **Mitigation:**
  - Every .zarc entry is signed with the RFE's signing key. Chain integrity is verified on every read.
  - `provenance_store.py` validates the entire chain before accepting any new entry; if a break is detected, the system enters a tamper-evident state and refuses further operation until manually cleared.
  - All writes go through a single writer, preventing race conditions.

## Security Monitoring
All rejected streams, signature failures, and config mismatches are logged as `security_event` entries in the .zarc journal. A dedicated endpoint streams these events for SIEM integration.
