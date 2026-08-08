# Governance Implementation Audit: From the Egregore Compact to the Current Egregore Codebase

**Date:** 2026-07-19  
**Scope:** Egregore repository (`/opt/egregore`) — mapping the speculative governance concepts from the *Inter-Species Oversight Committee* report to actual code, identifying gaps, and proposing implementation paths.  
**Status:** Study-phase draft; not a final design.  

---

## 1. Purpose

The *Inter-Species Oversight Committee* report describes an aspirational governance system for Egregore: a constitutional AI overseer (The Weave), a Tri-Cameral Mind, Ambient Contracts, Circuit Breakers, a Contribution Ledger, an Epistemic Commons with a Reality Arbitration Protocol, Horizon Deliberations, and AI memory/trauma management.

This audit answers three practical questions:

1. **What already exists** in the current Egregore codebase that corresponds to each concept?
2. **What is missing** and how big is the gap?
3. **How could the concept be implemented** in this codebase without violating its architecture, determinism, or fail-closed governance posture?

The verdict is that Egregore already has a surprisingly strong governance substrate — signed provenance, CBI-0 checkpoints, RFE arbitration, cell protocol, federation treaties, and freeze/circuit-breaker/load-regulator machinery. The aspirational concepts are largely **groundable** in that substrate, but several require new domain models and a unifying runtime layer that does not yet exist.

---

## 2. Executive Verdict

| Concept | Status in codebase | Implementation realism | Notes |
|---|---|---|---|
| **Constitutional AI Overseer / The Weave** | Partial | High | Constitution exists; active overseer loop is a stub. |
| **Tri-Cameral Mind** | Partial (taxonomy only) | High | Cells already group into `university`/`guild`/`investigation`; can be orchestrated into chambers. |
| **Ambient Contracts** | Not implemented | Medium | Needs new domain model + interpreter; can use `.zarc` as event log. |
| **Circuit Breaker AIs** | Partial, fragmented | High | Many safety pieces exist; need a unified coordinator. |
| **Contribution Ledger** | Partial (signatures, node trust) | Medium | Needs per-actor ledger and reputation-weighted routing. |
| **Epistemic Commons / RAP** | Strong (as RFE) | Very High | RFE engine already arbitrates conflicting evidence streams. |
| **Horizon Deliberations / North Star** | Not implemented | Medium | Needs goal registry + ratification workflow + alignment audit. |
| **AI Memory / Trauma Management** | Not implemented | Medium-Low | Needs case-outcome memory and failure-tagging; start small. |

The single most important missing piece is a **unified Meta-Governor runtime** that can collect quorum votes, authorize overrides, and coordinate the fragmented safety mechanisms.

---

## 3. Core substrate already present

Before mapping concepts, identify the actual governance machinery in the repo.

### 3.1 Signed provenance chain (`.zarc`)

- **Files:** `src/egregore/kernel/provenance.py`, `src/egregore/infrastructure/zarc_journal.py`, `src/egregore/domain/provenance_model.py`.
- **What it does:** Writes canonical JSON events with SHA-256 `prev_hash` chains and Ed25519 signatures. Supports `verify_chain()`.
- **Governance role:** This is the closest existing analog to a **shared, tamper-evident ledger** for any governance concept. It should be the event log for contracts, votes, overrides, freezes, and contribution events.

### 3.2 CBI-0 fail-closed checkpoints

- **Files:** `src/egregore/governance/cbi_zero.py` (or equivalent enforcement module), `tests/test_cbi_0_enforcement.py`, `docs/adr/ADR-00X-cbi-zero.md`.
- **What it does:** M1–M4 checkpoints enforce architecture boundaries (e.g., domain cannot import infrastructure). Tests fail if dependency direction is violated.
- **Governance role:** This is a **hard-coded negative veto** — similar in spirit to The Weave's ability to strike down violations, but at the code-dependency level rather than the political level.

### 3.3 Reproducible Fusion Engine (RFE)

- **Files:** `src/egregore/rfe/engine.py`, `src/egregore/rfe/arbitration.py`, `src/egregore/rfe/models.py`, `src/egregore/rfe/security.py`, `config/rfe_config.yaml`, `docs/arbitration_spec.md`.
- **What it does:** Takes multiple evidence streams, scores them by authority, confidence, freshness, corroboration, and decay; resolves conflicts; produces a signed report and decision log.
- **Governance role:** This is already a **Reality Arbitration Protocol**. It determines which version of reality (which evidence stream) wins when sources conflict.

### 3.4 Cell protocol and Ombudsman routing

- **Files:** `src/egregore/cells/models.py`, `src/egregore/cells/registry.py`, `src/egregore/cells/executor.py`, `src/egregore/interface/ombudsman_router.py`, `src/egregore/governance/cell_protocol.py`, `cells/*/spec.yaml`.
- **What it does:** Cells are typed units (`university`, `guild`, `investigation`, `legal`, `audit`) with taxonomies, stage gates, dependencies, advisory relationships, load limits, and verification rules. The Ombudsman dispatches requests to matching cells and fuses their outputs through RFE.
- **Governance role:** This is the substrate for the **Tri-Cameral Mind**. The chambers are not separate institutions; they are routing policies over existing cell types.

### 3.5 Federation constitution and treaties

- **Files:** `config/egregore_constitution.yaml`, `docs/constitution/egregore_constitution.md`, `src/egregore/domain/federation_constitution.py`, `src/egregore/application/federation_treaty.py`, `src/egregore/application/entropy_exchange.py`, `src/egregore/application/escalation_service.py`, `src/egregore/http_api/http/v1/federation.py`.
- **What it does:** Defines articles, required treaty clauses, entropy thresholds, escalation levels, quorum config, treaty proposal/ratification, and freeze-on-critical behavior.
- **Governance role:** This is the **Egregore Compact** in embryo. It is currently static configuration plus passive storage; it lacks an active overseer loop.

### 3.6 Safety and freeze machinery

- **Files:** `src/egregore/patterns/circuit_breaker.py`, `src/egregore/shared/freeze_state.py`, `src/egregore/interface/dashboard/freeze_middleware.py`, `src/egregore/application/escalation_service.py`, `src/egregore/powertrain/thermal_governor.py`, `src/egregore/application/thermal_governor_service.py`, `src/egregore/powertrain/load_regulator.py`, `src/egregore/application/admission_controller.py`, `src/egregore/governance/anchorum_integrity_gate.py`.
- **What it does:** Circuit breakers, SEL-X freeze state machine, dashboard freeze middleware, thermal/load/admission throttling, integrity gates.
- **Governance role:** These are the **Circuit Breaker AIs** of the aspirational report, but they are fragmented and not centrally coordinated.

### 3.7 Node trust and federation mesh

- **Files:** `src/egregore/application/node_registry.py`, `src/egregore/application/federation_mesh.py`.
- **What it does:** Computes node `trust_score` from evidence success/freshness/breadth; tracks violations and bans/suspects.
- **Governance role:** Seed material for a **Contribution Ledger** or reputation system.

---

## 4. Concept-by-concept audit

### 4.1 The Egregore Compact / The Weave (Constitutional AI Overseer)

**Aspirational description:** A runtime environment for law with a hard-coded veto over any action violating axioms: Right to Cognitive Integrity, Mutual Survival, Equal Contestation.

**What exists:**
- `config/egregore_constitution.yaml` and `docs/constitution/egregore_constitution.md` define articles, treaty clauses, entropy thresholds, escalation levels, and quorum requirements.
- `src/egregore/domain/federation_constitution.py` models the constitution as code.
- `src/egregore/application/escalation_service.py` logs escalations to `.zarc` and can freeze on `CRITICAL`/`OVERRIDE`.
- `src/egregore/meta_governor.py` is a **stub**.

**What is missing:**
- No active overseer loop that continuously evaluates actions against the constitution.
- `OVERRIDE` quorum is config only; no vote collection or signature verification.
- No autonomous treaty-compliance monitor.

**Implementation path:**
1. Implement `MetaGovernorService` in `src/egregore/application/` (allowed to orchestrate domain and interface ports, but not import infrastructure directly).
2. Define `QuorumVote` and `OverrideAuthorization` domain models.
3. Collect signed votes as `.zarc` events; verify signatures and quorum via `FederationConstitution` rules.
4. Wire `MetaGovernorService` into `EscalationService` so `OVERRIDE` and `CRITICAL` escalations require and receive authorized quorum votes before unfreezing or executing.
5. Add a `/v1/governance/compact/check` endpoint that evaluates a proposed action against active treaty clauses and returns `ALLOWED`/`VETOED`/`REQUIRES_QUORUM`.

**Critical notes:** The constitution is currently static YAML. Turning it into an active overseer is realistic, but care must be taken not to make the overseer a single point of failure. The fail-closed posture should be: if the overseer cannot verify compliance, the action is blocked.

---

### 4.2 The Tri-Cameral Mind

**Aspirational description:** Three branches — Human Assembly, AI Conclave, Interface Synod — sharing proposal and mediation power.

**What exists:**
- Cell taxonomy already groups cells into `university`, `guild`, `investigation`, `legal`, `audit`.
- `src/egregore/interface/ombudsman_router.py` dispatches by taxonomy and fuses outputs via `reproducible_fusion()`.
- `src/egregore/application/agent_registry.py` registers human/AI-invokable agents.

**What is missing:**
- No explicit three-chamber deliberation protocol.
- No inter-chamber consensus rule.
- No representative selection from each chamber for a given decision.

**Implementation path:**
1. Create `TriCameralCouncil` application service.
2. For a high-stakes request, select one representative cell from each of three chambers (e.g., `university` → Human Assembly analog; `investigation` + AI-hosted cells → AI Conclave analog; `legal` + `audit` → Interface Synod analog).
3. Dispatch the request to each representative via `CellExecutor`/`OmbudsmanRouter`.
4. Collect the resulting RFE streams and run `reproducible_fusion()` with a chamber-arbitration policy.
5. Require agreement from at least two chambers for binding action; persist the deliberation record to `.zarc`.
6. Expose `/v1/governance/tricameral/deliberate`.

**Critical notes:** This is a thin orchestration layer over existing pieces. The risk is semantic: cells are not literally human or AI assemblies, so the metaphor must be clearly documented. The value is that it formalizes cross-vertical conflict resolution.

---

### 4.3 Ambient Contracts

**Aspirational description:** Background smart contracts mediating everything from energy distribution to personal data use.

**What exists:**
- `src/egregore/application/federation_treaty.py` has passive treaty propose/ratify/store.
- `.zarc` is an append-only signed event log.

**What is missing:**
- No contract interpreter.
- No automatic obligation evaluation.
- No trigger/sanction execution.

**Implementation path:**
1. Define `AmbientContract` domain model: parties, obligations, triggers, sanctions, effective_window.
2. Build `AmbientContractInterpreter` (application layer) that reads active contracts and evaluates obligations against incoming `.zarc` events.
3. Obligations can be expressed as predicate rules over event types/payloads (e.g., "no more than X inference tokens per hour without quorum approval").
4. Violations emit `ContractViolationEvent` to `.zarc` and trigger `EscalationService` / freeze.
5. Integrate with `EntropyExchange` and treaty logic so contracts can reference federation state.

**Critical notes:** Avoid building a blockchain VM. Egregore's determinism and `.zarc` replay give you most of what you need. Keep contracts as declarative rules, not arbitrary code.

---

### 4.4 Circuit Breaker AIs

**Aspirational description:** AI monitors that slow or freeze contract execution when emergent harm is detected.

**What exists:**
- `src/egregore/patterns/circuit_breaker.py`
- `src/egregore/shared/freeze_state.py` (SEL-X state machine)
- `src/egregore/interface/dashboard/freeze_middleware.py`
- `src/egregore/application/escalation_service.py`
- `src/egregore/powertrain/thermal_governor.py`
- `src/egregore/application/thermal_governor_service.py`
- `src/egregore/powertrain/load_regulator.py`
- `src/egregore/application/admission_controller.py`
- `src/egregore/governance/anchorum_integrity_gate.py`

**What is missing:**
- No unified safety circuit coordinator.
- These components do not talk to each other.
- Inference admission is not throttled by entropy/escalation/freeze state.

**Implementation path:**
1. Create `SafetyCircuitCoordinator` in `src/egregore/application/`.
2. Subscribe it to freeze state, escalation state, thermal state, load-regulator state, and circuit-breaker state.
3. Define clamp rules: if any critical signal fires, throttle or reject new inference admissions; if multiple signals fire, trigger freeze.
4. Expose `/health/safety` returning the composite safety state and the active signals.
5. Add hysteresis and cooldown to avoid cascade trips.

**Critical notes:** This is high-value and realistic. The pieces are already there; they just need a conductor. The architecture-policy tests will need updating because `application/` importing `powertrain/` may require explicit allowlisting.

---

### 4.5 Contribution Ledger

**Aspirational description:** AI-mediated system measuring value added to the collective, determining bandwidth, computational priority, and voting weight.

**What exists:**
- Treaty signatures per party in `federation_treaty.py`.
- `src/egregore/application/node_registry.py` computes `trust_score` from evidence success/freshness/breadth.
- `src/egregore/application/federation_mesh.py` tracks violations and bans/suspects.
- `.zarc` is an append-only signed ledger of all events.

**What is missing:**
- No per-human or per-agent contribution ledger.
- No reputation-weighted cell routing.
- No slashing for verified bad contributions.
- No "Notary Nodes" for attestation.

**Implementation path:**
1. Define `ContributionLedger` domain model: actor_id, contribution_hash, action_type, verification_status, value_vector, timestamp.
2. Back it with `Provenance`/`ZarcJournal` so every entry is signed and chained.
3. Integrate with RFE source-tier weights: high-reputation actors get higher effective tier.
4. Route cells preferentially by trust score (extend `OmbudsmanRouter.select_least_loaded()` to include reputation).
5. Define freeze/slash rules: verified bad contributions (e.g., contradicted by RFE arbitration) reduce reputation and can suspend the actor.

**Critical notes:** This is the most politically sensitive concept. The aspirational report itself warns of "truth-credit oligopoly" and "cognitive gig economy." Any implementation must separate **epistemic reputation** from **economic contribution** and must not let high-credit actors override RFE arbitration.

---

### 4.6 Epistemic Commons / Reality Arbitration Protocol (RAP)

**Aspirational description:** Shared reality substrate with Notary Nodes and a Reality Arbitration Protocol that convenes a jury to resolve conflicting realities.

**What exists:**
- `src/egregore/rfe/engine.py` implements `reproducible_fusion()`.
- `src/egregore/rfe/arbitration.py` resolves conflicts.
- `src/egregore/rfe/models.py` defines streams, authority assessments, scored streams, conflicts, sensitivity appendix.
- `src/egregore/rfe/security.py` handles signatures and source-flooding/duplicate detection.
- `src/egregore/interface/anchorum_router.py` fuses ANCHORUM forensic output through RFE.
- `config/rfe_config.yaml` defines scoring weights, tiers, decay, thresholds.

**What is missing:**
- RAP as an explicit distributed protocol across nodes.
- Cross-node manifest exchange.
- Foundational Evidence Authority (FEA) key registry is referenced but not enforced.
- Live red-team harness.
- Human feedback loop is stubbed.

**Implementation path:**
1. Extend RFE with cross-node signed stream notarization: each node signs the streams it forwards, and manifests are exchanged via the federation mesh.
2. Implement the FEA key registry in `rfe/security.py` and enforce it during authority assessment.
3. Add a live red-team harness that periodically injects adversarial streams and verifies RFE resolves them correctly.
4. Wire `feedback_to_stream()` into a `/api/v1/rfe/feedback` endpoint so human/AI jurors can submit corrective feedback that becomes a new evidence stream.
5. Brand the existing RFE pipeline as the operational RAP; the "jury" becomes a federation of cells + feedback streams rather than a single convened panel.

**Critical notes:** RFE is the strongest existing analog. It can be marketed as RAP once federation and feedback loops are added. The risk of "truth cartels" is mitigated by RFE's signature/tier/corroboration logic, but only if FEA keys are actually enforced.

---

### 4.7 Horizon Deliberations / North Star

**Aspirational description:** Long-term goal-setting through AI-facilitated simulations and public assemblies, producing culturally powerful "North Star" statements.

**What exists:**
- Constitution preamble; no long-term goal registry in code.

**What is missing:**
- Goal registry.
- Ratification workflow.
- Alignment auditor that checks cell outputs against active North Stars.

**Implementation path:**
1. Define `HorizonRegistry` domain model storing `NorthStarStatement`s with `id`, `statement`, `ratified_at`, `review_deadline`, `status`.
2. Create a ratification workflow using `MetaGovernorService` quorum.
3. Add an `AlignmentAuditor` that flags RFE conclusions or cell outputs that contradict active North Stars.
4. Tie North Stars to RFE `constraints` so they are not ignored doc artifacts.
5. Emit ratification and alignment events to `.zarc`.

**Critical notes:** Start with one or two binding North Stars (e.g., "fail closed on constitutional ambiguity"). Avoid proliferating vague statements. The alignment auditor must be deterministic and version-pinned.

---

### 4.8 AI Memory / Trauma Management

**Aspirational description:** Managed episodic memory for AIs, trauma tagging after repeated failures, and slow "right to be forgotten" processing.

**What exists:**
- Models have weights and cell context, but no managed episodic memory registry.
- `src/egregore/infrastructure/sediment_archive.py` archives dead agencies.

**What is missing:**
- Case-outcome memory.
- Trauma/failure tagging.
- Shadow-mode requirement before re-enabling faulty cells/models.

**Implementation path:**
1. Define `MemoryStratum` domain model: case_id, cell_id, model_id, outcome, operator_correction, freeze_events, timestamp.
2. After each cell execution, write the outcome to `MemoryStratum` and to `.zarc`.
3. If a cell/model accumulates repeated failures or freezes, tag it with `trauma` status.
4. Require a red-team replay (against historical cases + synthetic adversarial cases) before re-enabling a traumatized cell/model.
5. Integrate with `SedimentArchive` for long-term dead-agency storage.

**Critical notes:** This is the fuzziest concept. Start small with freeze-history + case-corrections; do not build a general AI memory system. The goal is operational safety, not artificial sentience modeling.

---

## 5. Cross-cutting architecture constraints

Any implementation must respect the existing architecture:

1. **Layer separation.** `governance/` may only import `shared/`; `application/` may not import `infrastructure/` except via allowlisted ports. New services must live in the correct layer.
2. **Determinism.** Voting, arbitration, and ledger updates must be replay-correct. Use `shared/canonical.py` for JSON serialization.
3. **Fail-closed.** When in doubt, freeze or veto. This aligns with The Weave's defensive role.
4. **Version pinning.** RFE and cell execution are version-pinned. New governance rules must be versioned similarly.
5. **Test policy.** `tests/test_arch_enforcement.py` and `tests/test_architecture_policy_intent.py` enforce import rules. New cross-layer wiring will require test updates.

---

## 6. Implementation priorities (realistic roadmap)

| Phase | Weeks | Deliverable | Unlocks |
|-------|-------|-------------|---------|
| 0 | 2–3 | `MetaGovernorService` + quorum vote domain model | Constitutional overseer, Horizon ratification, OVERRIDE handling. |
| 1 | 2–3 | `SafetyCircuitCoordinator` | Unified circuit-breaker/freeze/load/thermal/admission response. |
| 2 | 3–4 | `TriCameralCouncil` orchestration layer | Cross-chamber deliberation over existing cells. |
| 3 | 3–4 | Extend RFE with cross-node notarization + FEA registry + feedback endpoint | Operational Reality Arbitration Protocol. |
| 4 | 3–4 | `AmbientContract` interpreter + contract event rules | Background obligation monitoring. |
| 5 | 4–6 | `ContributionLedger` + reputation-weighted routing | Contribution/reputation governance. |
| 6 | 2–3 | `HorizonRegistry` + `AlignmentAuditor` | North Star binding and drift detection. |
| 7 | 4–6 | `MemoryStratum` + trauma replay gate | AI memory/trauma management (start small). |

---

## 7. Risks and cautions

1. **Do not build a blockchain.** Egregore's `.zarc` + provenance + deterministic replay already provide ledger properties. A separate chain would duplicate state and violate the architecture.
2. **Avoid over-centralizing the Meta-Governor.** It should be a coordinator of signed events, not a singular oracle. Its failure mode must be freeze, not arbitrary rule.
3. **Separate epistemic reputation from economic contribution.** The aspirational report explicitly warns that linking truth to economic power creates "truth cartels."
4. **Keep contracts declarative.** Arbitrary smart-contract code would introduce undecidable behavior into a deterministic runtime.
5. **Respect the existing test matrix.** Adding governance ports without updating architecture-policy tests will break CI.

---

## 8. Conclusion

The aspirational governance system described by the Inter-Species Oversight Committee is **not fantasy relative to the current codebase**. Egregore already owns the hardest pieces: a signed provenance chain, fail-closed CBI-0 enforcement, RFE-based evidence arbitration, typed cells with taxonomies, federation treaties, and a fragmented but real set of safety mechanisms.

The missing layer is a **coordinating runtime** that turns these pieces into an active governance system. The first priority is `MetaGovernorService`, followed by `SafetyCircuitCoordinator` and `TriCameralCouncil`. Once those exist, the more speculative concepts — Ambient Contracts, Contribution Ledger, Horizon Deliberations, and AI memory management — can be grounded in the existing substrate rather than invented from scratch.

The study-phase recommendation is to **prototype these three coordinators first** and measure whether they produce the governance behaviors described in the aspirational report before expanding to the full conceptual stack.
