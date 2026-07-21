# Egregore Data Governance Policy

## Scope

This document governs how Egregore handles legal and medical dossier data,
case law extracts, email intake, and persisted `.zarc` artifacts. It supports
control **C5.1 — Data Governance (GDPR/HIPAA/Retention/PII)**.

Egregore processes two sensitive categories:

1. **Legal dossiers** — attorney work product, evidence summaries, litigation
   holds, and case-law extracts.
2. **Medical records** — health information attached to legal cases (e.g.,
   workplace injury, malpractice, disability claims).

All data handling must satisfy the principles of lawful basis, data
minimization, purpose limitation, storage limitation, integrity, and
confidentiality.

---

## GDPR / HIPAA Compliance Matrix

| Requirement | Legal Dossiers | Medical Records | Implementation |
|-------------|----------------|-----------------|----------------|
| Lawful basis | Litigation legitimate interest / contract | Explicit consent or legal obligation | Verified at intake; consent ID recorded in `.zarc` payload |
| Data minimization | Only ingest case-relevant documents | Only ingest records strictly necessary for the claim | `DossierGenerateService` rejects oversized/unrelated payloads |
| Right to access | Case owner may request full dossier | Patient may request record copy | Export via `/api/v1/cases/{case_id}` |
| Right to erasure | Limited by litigation hold | Limited by medical retention law | Hold overrides erasure; audit logged |
| Integrity | Signed `.zarc` chain | Signed `.zarc` chain | `src/egregore/kernel/provenance.py` |
| Confidentiality | RBAC + KEK | RBAC + KEK + enhanced access logging | `src/egregore/application/rbac_authz_provider.py`, `src/egregore/infrastructure/cluster_kek.py` |

---

## Retention Schedule for `.zarc` Artifacts

`.zarc` files are the immutable journal of all dossier commits. Retention is
driven by case type and legal jurisdiction.

| Artifact type | Retention period | Rationale | Destruction |
|---------------|------------------|-----------|-------------|
| General legal dossier `.zarc` | `[ASK USER: e.g., 7 years post-case closure]` | Statute of limitations + appeals window | Cryptographic shred after hold release |
| Medical record `.zarc` | `[ASK USER: e.g., indefinite or jurisdiction-mandated period]` | HIPAA / provincial health record requirements | Requires medical records officer approval |
| Case-law extracts | `[ASK USER: e.g., 7 years or life of case]` | Derived from public sources but may contain annotations | Anonymize before destruction |
| Intake email raw copies | `[ASK USER: e.g., 30 days after extraction]` | Transient ingestion artifact | Deleted after canonical extraction verified |
| Failed ingestion logs | `[ASK USER: e.g., 90 days]` | Debugging + compliance evidence | Purged automatically |

Implementation note: `ZarcJournal` in `src/egregore/infrastructure/zarc_journal.py`
appends only; physical deletion of `.zarc` files is a governed operational
procedure, not a code path.

---

## Anonymization Policy for Case Law Data

Case-law extracts used for training, benchmarking, or public reporting must be
anonymized before leaving the Core Plane.

| Field | Action | Owner |
|-------|--------|-------|
| Party names | Replace with `PARTY_A`, `PARTY_B` | Ingestion pipeline |
| Case numbers | Hash to stable pseudonym | Ingestion pipeline |
| Dates | Shift to relative offsets or remove | Ingestion pipeline |
| PII in footnotes | Redact | Prompt hardening / output control |
| Attorney names | Replace with role labels | Ingestion pipeline |

Anonymization is verified by:
- `tests/test_audit_redaction.py` (if present)
- Manual spot-check during quarterly data-governance review

---

## PII Handling in `imap_connector.py`

`src/egregore/infrastructure/imap_connector.py` fetches email via IMAP4_SSL.
PII handling rules:

1. **Fetch scope:** Only `UNSEEN` messages up to a configured `limit` are
   fetched; the connector does not enumerate or retain mailbox metadata.
2. **Decoding:** Headers and bodies are decoded with `errors="replace"` to
   avoid leaking raw bytes on encoding failures.
3. **No local persistence:** `IMAPConnector` returns `IMAPMessage` dataclasses;
   persistence is delegated to the ingestion pipeline which applies PII tags.
4. **Transport:** IMAP4_SSL over port 993 is mandatory; plain IMAP is blocked.
5. **Credential handling:** Username/password are passed by the caller and are
   never logged or stored by the connector.

---

## Consent Management for Medical Records

Medical records require documented consent before ingestion.

| Step | Action | Record |
|------|--------|--------|
| 1. Consent capture | Patient or authorized representative signs consent | Consent ID + timestamp stored in case metadata |
| 2. Consent verification | Intake pipeline checks `medical_consent_id` | Logged in `.zarc` payload |
| 3. Withdrawal | Patient withdraws consent | Trigger litigation-hold review; if no hold, mark for retention-limited deletion |
| 4. Audit | Quarterly consent reconciliation | Report stored in `docs/audits/consent/` |

Withdrawal of consent does **not** override an active litigation hold. The
`LitigationHoldTrigger` in `src/egregore/governance/litigation_hold.py` takes
precedence and logs the conflict.

---

## Roles and Responsibilities

| Role | Responsibility |
|------|----------------|
| Data Protection Officer | Owns this policy, handles subject-rights requests |
| Security Lead | Owns KEK, encryption, and access reviews |
| SRE Lead | Owns retention automation, backup encryption, and deletion workflows |
| Legal/Governance Owner | Approves hold-based retention overrides |

---

## Review Cadence

- **Quarterly:** consent reconciliation, access review, retention log review
- **Annually:** full policy review and jurisdiction update

Last review: `[ASK USER: date]`  
Next review: `[ASK USER: date]`

---

## Reference Files

- `src/egregore/infrastructure/zarc_journal.py`
- `src/egregore/kernel/provenance.py`
- `src/egregore/infrastructure/imap_connector.py`
- `src/egregore/governance/litigation_hold.py`
- `src/egregore/application/rbac_authz_provider.py`
- `src/egregore/infrastructure/cluster_kek.py`
- `tests/test_audit_redaction.py`
