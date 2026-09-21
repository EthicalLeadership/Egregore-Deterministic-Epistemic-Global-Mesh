# Sealed evidence packages (BagIt profile)

A sealed package is the **portable, court-transferable** form of the
`.zarc` evidence substrate: payload + fixity manifests + a **signed
verification report** that an opposing expert can re-derive offline,
trusting nothing the package claims.

- Sealer: `src/egregore/application/evidence_sealer.py`
- Verifier: `src/egregore/application/evidence_package_verifier.py`
- CLIs: `scripts/seal_evidence_package.py`, `scripts/verify_evidence_package.py`
- Report schema: `src/egregore/domain/evidence_package.py`

## Layout

```
<package>/
  bagit.txt                      # BagIt-Version: 1.0 / UTF-8
  bag-info.txt                   # Source-Organization, Contact-Role (roles/entity only),
                                 # External-Identifier (seal_id), Bagging-Timestamp-Ns, Payload-Oxum
  manifest-sha256.txt            # "<hash>  data/<path>" per payload file (sorted)
  tagmanifest-sha256.txt         # fixity for all tag files (bagit, bag-info, manifest, report, sig, index)
  data/
    chains/<name>.zarc           # whole chain(s), unmodified
    blocks/blocks.zarc           # block chain (optional)
    anchors/anchors.json         # anchor records export (optional)
    custody/<evidence_id>.json   # custody histories (optional)
    witness/checkpoints.json     # witness checkpoints (optional)
  subject_index.json             # subject (case/evidence) → chain entry references
  verification_report.json       # machine-readable report (schema below)
  verification_report.sig        # {"algorithm":"ed25519", "fingerprint", "signature"}
```

## Trust model

1. **Fixity** — every payload byte is covered by `manifest-sha256.txt`;
   every tag file (including the report and its signature) by
   `tagmanifest-sha256.txt`. Tampering with any file breaks fixity.
2. **Independent evidence verification** — the verifier re-runs the actual
   evidence checks from the payload bytes: `.zarc` linkage + per-line
   signatures, block-chain linkage + block signatures, custody continuity,
   witness quorum, anchor TSA verification (when a trust dir is supplied).
3. **Signed report** — `verification_report.sig` is an Ed25519 signature
   over `sha256(canonical_json(verification_report.json))`, made by the
   sealing node's `ISigningBackend` (HSM-capable). It attests *what the
   node observed at seal time* — including failures. A report claiming
   success over corrupted payload is caught by check 2
   (`verdict_consistency`).
4. **Seal identity** — `seal_id = sha256(manifest-sha256.txt)`: payload
   fixity determines identity; the signer is bound by the report
   signature. Deterministic: same payload ⇒ same seal_id.

`verification_report.json` carries: seal_id, generated_at_ns, subject,
per-chain results (entries, head_hash, chain_valid), blocks result,
anchors summary (tsa_verified/tsa_failed/local/mock, trust_assessed),
custody continuity, witness quorum, fixity_entries, **verdict**,
**failures**, signer_fingerprint.

## Sealing

```bash
export EGREGORE_SIGNING_KEY_HEX=<node key>     # or EGREGORE_SIGNING_BACKEND=pkcs11
python scripts/seal_evidence_package.py \
    --chain main=~/egregore_data/pioneer1/main.zarc \
    --blocks ~/egregore_data/pioneer1/blocks.zarc \
    --anchors-db ~/egregore_data/pioneer1/node.db \
    --zarc ~/egregore_data/pioneer1/main.zarc --signing-key $EGREGORE_SIGNING_KEY_HEX \
    --subject case_id=MOLSON-2026 --subject evidence_id=EV-001 \
    --evidence-id EV-001 \
    --tsa-trust-dir config/tsa_trust \
    --out packages/MOLSON-2026-sealed --zip
```

Notes:
- `--evidence-id` records a custody `seal` event on the live chain whose
  `evidence_hash` is the `seal_id` — the chain itself testifies the
  package was sealed.
- A failing verification suite does **not** abort sealing: the report
  records `verdict: false` with enumerated failures and the signature
  attests that honest record. Sealing is refused only for missing inputs,
  a non-empty output dir, or no signing backend.
- Tier-2 anchors cannot be assessed without `--tsa-trust-dir`; sealing
  without it yields `verdict: false` (unassessed ≠ verified).

## Verifying (auditor side)

```bash
python scripts/verify_evidence_package.py MOLSON-2026-sealed.zip \
    --tsa-trust-dir config/tsa_trust --min-witnesses 1 --json
```

Exit codes: `0` verified · `1` failed · `2` package not found.
The auditor needs **only the package** (plus the TSA trust anchors for
timestamp assessment and the witness registry for quorum assessment).
Everything else — chain signatures, custody, fixity, report signature —
is verified from package contents alone. Chain lines are signature-checked
against the report's embedded `signer_fingerprint`.

## Limitations (disclosed)

- The package is as strong as the substrate it contains: local tier-1
  anchors are reported as self-asserted; mock anchors never count as
  verified.
- TSA verification is pinned-trust offline validation (no CRL/OCSP).
- WORM storage enforcement is a separate (pending) increment; fixity
  makes tampering detectable, not physically impossible.
- Packages contain the *evidence substrate* (chains, blocks, anchors,
  custody, witness data) — not ANCHORUM case documents.
