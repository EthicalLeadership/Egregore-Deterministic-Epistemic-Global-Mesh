# Reproducible Fusion Engine (RFE) Specification

## 1. Overview

The **Reproducible Fusion Engine (RFE)** is Egregore Plane 1's deterministic
fusion component. It fuses non-deterministic suggestions and deterministic facts
from a manifest of evidence streams into an auditable, PDF/A-1b-ready report
structure. Every output is signed and appended to a `.zarc` provenance chain.

**Idempotency definition:** A function is idempotent with respect to a report
when, given the same manifest and versioned configuration, repeated executions
produce byte-identical output: the same report structure, the same decision log,
and the same SHA-256 hashes.

## 2. Module Structure

| Module | Responsibility |
|--------|----------------|
| `egregore.rfe.engine` | Pure `reproducible_fusion(manifest, config)` function |
| `egregore.rfe.arbitration` | Conflict resolution with tier/freshness/corroboration/score rules |
| `egregore.rfe.vim` | Version Integration Module: diff + unified synthesis |
| `egregore.rfe.security` | Ed25519 signature verification + anomaly detection |
| `egregore.rfe.feedback` | Feedback ingestion (implemented in router) |
| `egregore.rfe.provenance_store` | `.zarc` persistence helpers |
| `egregore.rfe.templates/` | Jinja2 report section templates |
| `egregore.http_api.http.v1.rfe` | FastAPI router mounted at `/api/v1/rfe` |
| `egregore.tooling.deterministic_verification` | Canonical hashing + replay determinism wrappers |

## 3. API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/rfe/generate` | Submit manifest, receive report + decision log + hashes |
| `POST` | `/api/v1/rfe/feedback` | Submit feedback as a `human_feedback` stream |
| `GET`  | `/api/v1/rfe/config` | Return versioned config |
| `GET`  | `/api/v1/rfe/health` | Health check |
| `GET`  | `/api/v1/rfe/versions` | List past report versions from `.zarc` |

## 4. Input Manifest Schema

```json
{
  "case_id": "string",
  "timestamp": "ISO8601",
  "streams": [
    {
      "stream_id": "string",
      "type": "string",
      "source_tier": 1,
      "content": { "claim": "positive|negative|neutral", "subject": "...", "text": "..." },
      "confidence": 0.95,
      "provenance_hash": "sha256",
      "signature": "base64|hex",
      "timestamp": "ISO8601",
      "decay": {
        "method": "exponential",
        "half_life_hours": 720,
        "justification": "Witness testimony freshness policy per SOP 4.2"
      },
      "severity_impact": 0.7,
      "relevance_tags": ["tag1"]
    }
  ],
  "constraints": { "max_pages": 20, "required_sections": [...], "output_format": "pdf-a-1b", "language": "en" }
}
```

- `decay` is mandatory per stream. Missing decay defaults to `{"method": "unbounded"}`
  and the stream is flagged `decay_method: unbounded`.
- There is **no global decay setting** in `config/rfe_config.yaml`.

## 5. Maximum Expected Epistemic Value (MEEV)

The composite scoring function implements MEEV:

```
S(i) = w_auth(i) * (
    w_impact * impact(i)
  + w_freshness * freshness(i)
  + w_reliability * confidence(i)
  + w_corroboration * corroboration(i)
) / (w_impact + w_freshness + w_reliability + w_corroboration)
```

- `impact = severity_impact`
- `freshness = 0.5^(age_hours / half_life_hours)` for exponential decay; `1.0` for unbounded
- `reliability = confidence`
- `corroboration = min(corrob_count, 10) / 10`
- `w_auth` is the fixed authority weight for the stream's tier; it multiplies the
  entire expression and cannot be overridden by confidence.

Weights are published in `config/rfe_config.yaml`:

```yaml
scoring_weights:
  w_impact: 0.35
  w_freshness: 0.25
  w_reliability: 0.25
  w_corroboration: 0.15
```

## 6. Source Authority Tiers

| Tier | Examples | Authority Weight |
|------|----------|------------------|
| 1 | Court rulings, sworn testimony, certified audit records | 1.0 |
| 2 | Official government reports, medical records | 0.9 |
| 3 | Professional analyst reports, sensor telemetry | 0.8 |
| 4 | News media, expert opinion | 0.6 |
| 5 | Social media, unverified eyewitness accounts | 0.3 |

Tiers are fixed per manifest version. Spoofing (declared tier does not match
provenance) is detected via Ed25519 signature verification.

## 7. Arbitration Procedure

For each detected conflict (same `type`, opposing `claim` tags):

1. **Authority tier gap:** if tier difference >= 2, higher tier wins.
2. **Composite score:** if score gap >= `arbitration_threshold` (0.15), higher score wins.
3. **Dead band:** if score gap < `dead_band` (0.05), force dispute.
4. **Tie-breakers:** within `[dead_band, arbitration_threshold)`, compare freshness,
   then corroboration count.
5. **Otherwise:** declare dispute.

Unresolved conflicts appear in the `disputed` report section.

## 8. Security & Anomaly Detection

- **Signatures:** Ed25519 over canonical JSON of the stream (excluding `signature`).
  Unsigned or invalid-signature streams are accepted but authority weight is halved.
- **Future timestamps:** rejected with HTTP 422.
- **Duplicate provenance hashes:** flagged when duplicates appear within the
  configured duplication window.
- **Source flooding:** flagged when a single source type exceeds the configured
  rate threshold within the window.

## 9. Sensitivity Appendix

When any stream has finite exponential decay, the report includes an Appendix A
that recomputes MEEV with each finite-decay half-life varied by ±50%. It lists
which supported/opposed conclusions flip under the variation.

## 10. Trajectory Accountability

Trend annotations include a qualitative confidence label and interval plus the
disclaimer:

> "This is a retrospective assessment only; it does not predict future events
> and must not be used for extrapolation."

## 11. Feedback Loop

`POST /api/v1/rfe/feedback` returns a new stream of type `human_feedback`. The
client includes this stream in the next manifest's `streams` array for fusion.

## 12. Version Integration Module (VIM)

Given the latest manifest and a previous report's decision log, VIM produces:

- `diff_analysis`: added/removed/retained/changed streams; flipped conclusions.
- `current_best_synthesis`: unified conclusions across versions with version lineage.

## 13. Running Tests

```bash
# Red-team harness + idempotency replay + VIM
pytest tests/test_rfe_replay.py tests/test_rfe_vim.py tests/redteam/ -m redteam -v

# Full test suite (including existing Egregore tests)
pytest
```

## 14. Deployment

See `scripts/deploy_rfe.sh`. It installs dependencies, runs the red-team and
idempotency tests, and starts the API.
