# TSA trust anchor pinning procedure

Status: **one production anchor pinned** (FreeTSA root, 2026-08-03).
Trust directory: `config/tsa_trust/`. Verification code:
`src/egregore/infrastructure/tsa_verifier.py`.

## Currently pinned anchors

| File | Subject | SHA-256 fingerprint | Validity |
|---|---|---|---|
| `freetsa_root.pem` | `O = Free TSA, OU = Root CA, CN = www.freetsa.org, C = DE` | `A6:37:9E:7C:EC:C0:5F:AA:3C:BF:07:60:13:D7:45:E3:27:BB:BA:A3:8C:0B:9A:F2:24:69:D4:70:1D:18:AA:BC` | 2016-03-13 → 2041-03-07 |

Source: `https://freetsa.org/files/cacert.pem` (fetched over HTTPS,
2026-08-03). FreeTSA is the development TSA — see "Production TSAs" below
before relying on it for contested evidence.

## Pinning procedure (repeat for each TSA)

1. **Fetch** the TSA's root (and intermediate) CA certificates from the
   provider's official site over HTTPS:
   ```bash
   curl -sfO https://freetsa.org/files/cacert.pem
   ```
2. **Verify out-of-band**: confirm the fingerprint via a second channel
   (provider docs, phone, signed email). Record it in the table above:
   ```bash
   openssl x509 -in cacert.pem -noout -subject -issuer -dates -fingerprint -sha256
   ```
3. **Install** into `config/tsa_trust/` (PEM or DER).
4. **Point the runtime at it** (default is `config/tsa_trust`):
   ```bash
   export EGREGORE_TSA_TRUST_DIR=config/tsa_trust
   ```
5. **Smoke-test end-to-end** (submits a real RFC 3161 request and verifies
   the response cryptographically):
   ```bash
   .venv/bin/python - <<'EOF'
   import sys; sys.path.insert(0, "src")
   from egregore.services.anchor_orchestrator.timestamp_client import RFC3161TimestampClient
   import hashlib
   client = RFC3161TimestampClient("https://freetsa.org/tsr", trust_dir="config/tsa_trust")
   token = client.timestamp(hashlib.sha256(b"smoke").hexdigest())
   assert token.verified, token.verification.failures
   print("OK", token.timestamp_iso)
   EOF
   ```

### Recorded validation (2026-08-03)

Live smoke test against `https://freetsa.org/tsr`: **PASS** — tier-2
token, `verified=True`, gen_time `2026-08-05T01:06:07+00:00`, chain of 2
certificates reaching the pinned root, policy OID captured. Forgery paths
(tampered imprint, wrong nonce, untrusted chain, missing EKU, bad CMS
signature) are covered offline in
`tests/infrastructure/test_tsa_verifier.py` (12 tests).

## Production TSAs (before contested-evidence use)

FreeTSA is a free community service with no SLA and a self-published root.
For evidence you expect to defend, pin a TSA whose operating practices you
can put in front of a court:

- **eIDAS-qualified TSPs** (Entrust, DigiCert, Docusign, etc.) — strongest
  cross-border recognition, including Quebec/EU contexts.
- **National/government PKI** where a relationship exists.
- **Self-hosted RFC 3161 TSA** — operationally immediate, legally weaker
  (self-asserted time); acceptable as an additional anchor, not the sole
  one. Pin your own root here and disclose it as self-issued in
  `docs/evidence/court_grade.md`.

You may pin **multiple** anchors: tokens from any pinned TSA verify.
Keep anchors for as long as tokens issued under them must remain
verifiable (decades for litigation holds).

## Compromise response

If a TSA key is compromised: remove its anchor (fail-closed — dependent
anchors stop verifying), trigger a freeze review of all anchors issued
under that TSA, re-anchor affected block hashes under a trusted TSA, and
record the event as a custody-relevant incident.
