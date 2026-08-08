# TSA trust anchors

This directory holds the **pinned trust anchors** used to verify RFC 3161
timestamp tokens (`src/egregore/infrastructure/tsa_verifier.py`).

## Why pinning

Timestamp verification is fail-closed against this directory: a TSA token
only verifies if its signer certificate chains to one of these anchors.
An empty or missing directory means **no token verifies** — by design.

## Adding an anchor

1. Download the TSA's root (and intermediate) CA certificate from the
   provider's official site over HTTPS.
2. Verify its fingerprint out-of-band (provider documentation, second
   channel) before trusting it:
   ```bash
   openssl x509 -in root.pem -noout -fingerprint -sha256
   ```
3. Place the PEM/DER file here (`.pem`, `.crt`, `.cer`, `.der`).

For FreeTSA (default dev TSA): <https://freetsa.org> publishes its CA
certificates (`cacert.pem`) — verify and pin before production use.

## Operational rules

- Treat this directory as security-critical configuration: review changes
  like code, and record anchor changes as custody-relevant events.
- Rotation: add the new anchor, verify tokens validate, then remove the
  old one. Historical anchors must remain as long as tokens issued under
  them need to verify.
- Revocation is out of band (no CRL/OCSP in this phase) — if a TSA key is
  compromised, remove its anchor and treat all dependent anchors as
  suspect (freeze and re-anchor under a trusted TSA).
