#!/usr/bin/env python3
"""
Structural Completeness Audit Engine — C1 to C7
Hardened edition with auto-remediation, diff tracking, CI gate, trend graph.

Run:
    python3 structural_audit_engine.py <project_root>
    python3 structural_audit_engine.py <project_root> --ci
    python3 structural_audit_engine.py <project_root> --trend

The engine is project-agnostic and pattern-based. Drop it into any Python/FastAPI
repo to score structural completeness across 22 controls.
"""

from __future__ import annotations

import fnmatch
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional


class Grade(Enum):
    PRESENT = "PRESENT"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    NA = "N/A"


EXCLUDED_DIRS = {
    "node_modules", "venv", ".venv", "env", ".env",
    ".git", "__pycache__", ".pytest_cache", "dist", "build",
    ".tox", ".mypy_cache", ".ruff_cache", ".coverage",
    "htmlcov", "site-packages", "egg-info", ".eggs",
    ".next", ".nuxt", ".output", "target", "vendor"
}

EXCLUDED_EXTS = {".pyc", ".pyo", ".so", ".dylib", ".dll", ".class"}


@dataclass
class Control:
    id: str
    name: str
    category: str
    grade: Grade = Grade.MISSING
    evidence: List[str] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)
    files_found: List[str] = field(default_factory=list)
    remediation: str = ""


@dataclass
class AuditReport:
    project_root: str
    controls: List[Control] = field(default_factory=list)
    summary: Dict = field(default_factory=dict)
    previous_summary: Optional[Dict] = None
    delta: Dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "project_root": self.project_root,
            "summary": self.summary,
            "previous_summary": self.previous_summary,
            "delta": self.delta,
            "controls": [
                {
                    "id": c.id,
                    "name": c.name,
                    "category": c.category,
                    "grade": c.grade.value,
                    "evidence": c.evidence,
                    "gaps": c.gaps,
                    "files_found": c.files_found,
                    "remediation": c.remediation
                }
                for c in self.controls
            ]
        }


class StructuralAuditEngine:
    def __init__(self, project_root: str):
        self.root = Path(project_root).expanduser().resolve()
        self.all_files: List[Path] = []
        self.file_index: Dict[str, List[Path]] = {}
        self._index_files()

    def _should_exclude(self, p: Path) -> bool:
        rel = p.relative_to(self.root)
        for part in rel.parts:
            if part in EXCLUDED_DIRS:
                return True
        if p.suffix.lower() in EXCLUDED_EXTS:
            return True
        return False

    def _index_files(self):
        if not self.root.exists():
            print(f"ERROR: {self.root} does not exist", file=sys.stderr)
            return
        for p in self.root.rglob("*"):
            if p.is_file() and not self._should_exclude(p):
                self.all_files.append(p)
                ext = p.suffix.lower()
                self.file_index.setdefault(ext, []).append(p)

    def _find(self, patterns: List[str], max_depth: int = 12) -> List[Path]:
        results = []
        for p in self.all_files:
            rel = p.relative_to(self.root)
            depth = len(rel.parts)
            if depth > max_depth:
                continue
            for pat in patterns:
                if fnmatch.fnmatch(str(rel), pat) or fnmatch.fnmatch(p.name, pat):
                    results.append(p)
                    break
        return results

    def _grep(self, keywords: List[str], extensions: Optional[List[str]] = None) -> List[Path]:
        results = []
        candidates = self.all_files
        if extensions:
            candidates = [p for p in candidates if p.suffix.lower() in extensions]
        # Exclude the audit engine itself from keyword evidence to avoid false positives.
        skip_names = {"structural_audit_engine.py", "structural_audit.py"}
        for p in candidates:
            if p.name in skip_names:
                continue
            try:
                text = p.read_text(errors="ignore")
                lowered = text.lower()
                for kw in keywords:
                    if kw.lower() in lowered:
                        results.append(p)
                        break
            except Exception:
                continue
        return results

    def _has_any(self, patterns: List[str]) -> bool:
        return len(self._find(patterns)) > 0

    # ── C1 ───────────────────────────────────────────────────────────────────
    def audit_c1_context_diagram(self) -> Control:
        c = Control("C1.1", "Context Diagram", "C1")
        found = self._find(["*context*", "*diagram*", "*.puml", "*.mmd", "*C4*", "diagrams/*"])
        c.files_found = [str(p.relative_to(self.root)) for p in found[:20]]
        if found:
            c4 = self._grep(["C4", "System Context", "boundary", "actor", "external system"], [".md", ".puml", ".mmd"])
            if c4:
                c.grade = Grade.PRESENT
                c.evidence.append(f"C4/system-boundary language in {len(c4)} files")
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("Diagram files exist but lack C4/system-boundary language")
                c.remediation = "Add C4 System Context syntax to existing diagram files or create docs/diagrams/context.md with Mermaid/PlantUML C4 notation."
        else:
            c.gaps.append("No context diagram files found")
            c.remediation = "Create docs/diagrams/context.md with a C4 System Context diagram using Mermaid syntax."
        return c

    def audit_c1_api_contract(self) -> Control:
        c = Control("C1.2", "API Contract", "C1")
        openapi = self._find(["*openapi*", "*swagger*", "*.yaml", "*.yml"])
        openapi_hits = [p for p in openapi if "openapi" in p.name.lower() or "swagger" in p.name.lower()]
        routes = self._grep(["@app.get", "@app.post", "@app.put", "@app.delete", "APIRouter", "FastAPI(", "@router.get", "@router.post"], [".py"])
        c.files_found = [str(p.relative_to(self.root)) for p in (openapi_hits + routes)[:20]]
        if openapi_hits:
            c.grade = Grade.PRESENT
            c.evidence.append(f"OpenAPI/Swagger spec file(s): {len(openapi_hits)}")
        elif routes:
            c.grade = Grade.PARTIAL
            c.evidence.append(f"FastAPI/APIRouter routes in {len(routes)} files")
            c.gaps.append("No explicit OpenAPI spec file")
            c.remediation = "Generate docs/openapi.json from the FastAPI app and commit it to version control."
        else:
            c.gaps.append("No API contract artifacts found")
            c.remediation = "Create docs/openapi.yaml or docs/openapi.json documenting all API endpoints, schemas, and auth."
        return c

    def audit_c1_decision_records(self) -> Control:
        c = Control("C1.3", "Decision Records (ADR)", "C1")
        adr = self._find(["*adr*", "*decision*", "docs/adr/*", "adr/*"])
        adr_md = [p for p in adr if p.suffix == ".md"]
        c.files_found = [str(p.relative_to(self.root)) for p in adr_md[:20]]
        if adr_md:
            struct = self._grep(["Status:", "Context:", "Decision:", "Consequences:", "Accepted", "Proposed", "Deprecated"], [".md"])
            if struct:
                c.grade = Grade.PRESENT
                c.evidence.append(f"Structured ADR format in {len(struct)} files")
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("Decision files lack structured ADR format")
                c.remediation = "Restructure existing decision files to ADR format: Status / Context / Decision / Consequences."
        else:
            c.gaps.append("No ADR files found")
            c.remediation = "Create docs/adr/ directory with ADR-0001-*.md files using a standard template."
        return c

    # ── C2 ───────────────────────────────────────────────────────────────────
    def audit_c2_runbook(self) -> Control:
        c = Control("C2.1", "Run Book / SOS", "C2")
        rb = self._find(["*runbook*", "*run book*", "*sos*", "*incident*response*", "*playbook*", "ops/*", "sre/*"])
        c.files_found = [str(p.relative_to(self.root)) for p in rb[:20]]
        if rb:
            deep = self._grep(["escalation", "incident category", "severity", "post-mortem", "rollback", "contact"], [".md", ".rst", ".txt"])
            if deep:
                c.grade = Grade.PRESENT
                c.evidence.append(f"Incident/escalation language in {len(deep)} files")
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("Runbook lacks incident categories or escalation matrix")
                c.remediation = "Add SEV1-4 severity levels, escalation matrix, per-component recovery procedures, and post-mortem template."
        else:
            c.gaps.append("No runbook or incident response docs found")
            c.remediation = "Create docs/runbook.md with severity levels, escalation matrix, per-component procedures, and rollback triggers."
        return c

    def audit_c2_performance_budget(self) -> Control:
        c = Control("C2.2", "Performance Budget", "C2")
        perf = self._find(["*performance*", "*latency*", "*throughput*", "*benchmark*", "*budget*", "*slo*", "*sla*"])
        perf_files = [p for p in perf if p.suffix in [".md", ".py", ".yaml", ".yml", ".json", ".txt"]]
        timing = self._grep(["benchmark", "latency", "throughput", "perf_counter", "timeit", "pytest-benchmark"], [".py"])
        c.files_found = [str(p.relative_to(self.root)) for p in (perf_files + timing)[:20]]
        if perf_files or timing:
            budget = self._grep(["p95", "p99", "latency budget", "RPS", "QPS", "requests per second", "MB/s", "GB/s"], [".md", ".py", ".yaml"])
            if budget:
                c.grade = Grade.PRESENT
                c.evidence.append(f"Explicit budget metrics in {len(budget)} files")
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("No explicit latency/throughput budget numbers")
                c.remediation = "Add explicit p95/p99 latency targets, RPS/QPS throughput, and resource limits to docs/performance.md."
        else:
            c.gaps.append("No performance budget docs or benchmark tests found")
            c.remediation = "Create docs/performance.md with latency/RPS budgets and add benchmark tests."
        return c

    def audit_c2_security_model(self) -> Control:
        c = Control("C2.3", "Security Model", "C2")
        sec = self._find(["*security*", "*threat*model*", "*auth*", "*authz*", "*crypt*", "*encryption*", "*secret*", "*vault*", "*tls*"])
        sec_files = [p for p in sec if p.suffix in [".md", ".py", ".yaml", ".yml", ".json", ".txt"]]
        crypto = self._grep(["ed25519", "rsa", "aes", "hmac", "hashlib", "secrets.", "cryptography", "jwt", "oauth", "bcrypt"], [".py"])
        c.files_found = [str(p.relative_to(self.root)) for p in (sec_files + crypto)[:20]]
        if sec_files:
            if crypto:
                c.grade = Grade.PRESENT
                c.evidence.append(f"Security docs + crypto implementation in {len(crypto)} files")
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("Security docs exist but no cryptographic implementation detected")
                c.remediation = "Add cryptographic controls (signing, encryption at rest/in transit) or document why they are not needed."
        else:
            c.gaps.append("No security model or threat model documentation")
            c.remediation = "Create docs/security/threat_model.md with STRIDE analysis, trust boundaries, and controls matrix."
        return c

    def audit_c2_resilience_pattern(self) -> Control:
        c = Control("C2.4", "Resilience Patterns", "C2")
        res = self._grep(["circuit breaker", "bulkhead", "retry", "backoff", "timeout", "dead letter", "failover", "graceful degradation", "health check", "CircuitBreaker", "RetryPolicy"], [".py", ".md", ".yaml"])
        c.files_found = [str(p.relative_to(self.root)) for p in res[:20]]
        if res:
            c.grade = Grade.PRESENT
            c.evidence.append(f"Resilience patterns in {len(res)} files")
        else:
            c.gaps.append("No circuit breaker, bulkhead, retry, backoff, or health check patterns")
            c.remediation = "Add retry with exponential backoff to external connectors and implement health checks for services."
        return c

    def audit_c2_scalability(self) -> Control:
        c = Control("C2.5", "Scalability Analysis", "C2")
        scale = self._find(["*scale*", "*horizontal*", "*vertical*", "*shard*", "*replica*", "*cluster*", "*node*", "*worker*", "*queue*"])
        scale_files = [p for p in scale if p.suffix in [".md", ".py", ".yaml", ".yml", ".json", ".txt"]]
        horizontal = self._grep(["horizontal", "shard", "replica", "partition", "worker pool", "thread pool", "asyncio", "concurrent"], [".py", ".md"])
        c.files_found = [str(p.relative_to(self.root)) for p in (scale_files + horizontal)[:20]]
        if scale_files:
            if horizontal:
                c.grade = Grade.PRESENT
                c.evidence.append(f"Horizontal scaling/scaling primitives in {len(horizontal)} files")
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("No explicit horizontal scaling or bottleneck analysis")
                c.remediation = "Document horizontal scaling strategy and bottleneck analysis in docs/scalability.md."
        else:
            c.gaps.append("No scalability documentation or scaling primitives")
            c.remediation = "Create docs/scalability.md with horizontal/vertical scaling strategy and bottleneck analysis."
        return c

    # ── C3 ───────────────────────────────────────────────────────────────────
    def audit_c3_use_cases(self) -> Control:
        c = Control("C3.1", "Use Cases Governed", "C3")
        uc = self._find(["*use*case*", "*usecase*", "*scenario*", "*story*", "*requirement*", "*functional*spec*", "*behavior*"])
        uc_files = [p for p in uc if p.suffix in [".md", ".py", ".yaml", ".yml", ".json", ".txt"]]
        sm = self._grep(["state machine", "stateMachine", "@state", "transition", "guard", "invariant", "precondition", "postcondition"], [".py", ".md"])
        c.files_found = [str(p.relative_to(self.root)) for p in (uc_files + sm)[:20]]
        if uc_files:
            if sm:
                c.grade = Grade.PRESENT
                c.evidence.append(f"State machine/governed behavior in {len(sm)} files")
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("Use cases exist but no state machine or guards")
                c.remediation = "Add state-machine guards, invariants, and pre/post conditions to use case documentation."
        else:
            c.gaps.append("No use case or behavioral specification files")
            c.remediation = "Create docs/use_cases.md with use cases, pre/post conditions, invariants, and state transitions."
        return c

    def audit_c3_error_handling(self) -> Control:
        c = Control("C3.2", "Error Handling", "C3")
        exc = self._grep(["class.*Exception", "class.*Error", "raise ", "except ", "try:", "finally:"], [".py"])
        degrade = self._grep(["degradation", "fallback", "fail-soft", "fail-safe", "stub mode", "graceful", "compensat", "saga"], [".py", ".md"])
        c.files_found = [str(p.relative_to(self.root)) for p in (exc + degrade)[:20]]
        if exc:
            if degrade:
                c.grade = Grade.PRESENT
                c.evidence.append(f"Exception handling + degradation strategies in {len(degrade)} files")
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("No explicit degradation modes or fallback strategies")
                c.remediation = "Add fallback strategies and graceful degradation modes to core engine and external connectors."
        else:
            c.gaps.append("No exception handling or error taxonomy detected")
            c.remediation = "Create an exception taxonomy and document degradation modes."
        return c

    def audit_c3_sequence_diagrams(self) -> Control:
        c = Control("C3.3", "Sequence Diagrams", "C3")
        seq = self._find(["*sequence*", "*seq*", "*.puml", "*.mmd", "*flow*", "*interaction*"])
        seq_files = [p for p in seq if p.suffix in [".md", ".puml", ".mmd", ".txt"]]
        syntax = self._grep(["sequenceDiagram", "->>", "-->>", "activate", "deactivate", "participant", "loop", "alt"], [".md", ".puml", ".mmd"])
        c.files_found = [str(p.relative_to(self.root)) for p in (seq_files + syntax)[:20]]
        if seq_files:
            if syntax:
                c.grade = Grade.PRESENT
                c.evidence.append(f"Sequence diagram syntax in {len(syntax)} files")
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("Files exist but no sequence diagram syntax")
                c.remediation = "Add Mermaid sequenceDiagram syntax to existing flow documentation."
        else:
            c.gaps.append("No sequence diagram or interaction flow documentation")
            c.remediation = "Create docs/sequences/ingestion.md with Mermaid sequence diagrams for key flows."
        return c

    # ── C4 ───────────────────────────────────────────────────────────────────
    def audit_c4_monitoring(self) -> Control:
        c = Control("C4.1", "Monitoring & Observability", "C4")
        mon = self._find(["*monitor*", "*observ*", "*metric*", "*log*", "*trace*", "*telemetry*", "*prometheus*", "*grafana*", "*jaeger*", "*sentry*"])
        mon_files = [p for p in mon if p.suffix in [".py", ".md", ".yaml", ".yml", ".json", ".txt"]]
        telem = self._grep(["prometheus", "grafana", "metrics", "histogram", "counter", "gauge", "span", "trace", "logger", "logging", "telemetry", "observability", "pulse", "health"], [".py", ".yaml", ".md"])
        c.files_found = [str(p.relative_to(self.root)) for p in (mon_files + telem)[:20]]
        if mon_files:
            if telem:
                c.grade = Grade.PRESENT
                c.evidence.append(f"Telemetry implementation in {len(telem)} files")
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("Monitoring docs exist but no telemetry implementation")
                c.remediation = "Add metrics/counters and document SLIs/SLOs in docs/monitoring.md."
        else:
            c.gaps.append("No monitoring, observability, or telemetry configuration")
            c.remediation = "Create docs/monitoring.md with SLIs/SLOs and add Prometheus/health-check instrumentation."
        return c

    def audit_c4_rollback(self) -> Control:
        c = Control("C4.2", "Rollback Strategy", "C4")
        rb = self._find(["*rollback*", "*blue*green*", "*canary*", "*feature*flag*", "*deployment*", "*release*", "*revert*"])
        rb_files = [p for p in rb if p.suffix in [".md", ".py", ".yaml", ".yml", ".sh", ".txt"]]
        strat = self._grep(["blue-green", "canary", "feature flag", "kill switch", "rollback", "revert", "rollback plan"], [".md", ".yaml", ".py"])
        c.files_found = [str(p.relative_to(self.root)) for p in (rb_files + strat)[:20]]
        if rb_files:
            if strat:
                c.grade = Grade.PRESENT
                c.evidence.append(f"Rollback strategies in {len(strat)} files")
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("No explicit rollback strategy documented")
                c.remediation = "Document rollback procedure, feature flags, and kill switches in deploy/rollback.md."
        else:
            c.gaps.append("No rollback strategy or deployment documentation")
            c.remediation = "Create deploy/rollback.md with blue-green/canary strategy and feature-flag kill switches."
        return c

    def audit_c4_deployment_pipeline(self) -> Control:
        c = Control("C4.3", "Deployment Pipeline", "C4")
        actions = self._find([".github/workflows/*", ".gitlab-ci*", "Jenkinsfile*"])
        actions = [p for p in actions if ".github" in str(p) or "jenkins" in p.name.lower()]
        ci = self._find(["*.yml", "*.yaml"])
        ci = [p for p in ci if "ci" in p.name.lower() or "cd" in p.name.lower() or "deploy" in p.name.lower()]
        docker = self._find(["Dockerfile*", "docker-compose*"])
        pipeline_doc = self.root / "docs" / "pipeline.md"
        has_pipeline_doc = pipeline_doc.exists()
        c.files_found = [str(p.relative_to(self.root)) for p in (actions + ci + docker + ([pipeline_doc] if has_pipeline_doc else []))[:20]]
        if actions or ci or docker or has_pipeline_doc:
            stages = self._grep(["stage", "gate", "build", "test", "deploy", "promote", "artifact", "provenance", "verify"], [".yml", ".yaml", ".md"])
            has_ci = bool(actions) or bool(ci)
            if stages and (has_ci or has_pipeline_doc):
                if has_ci and has_pipeline_doc:
                    c.grade = Grade.PRESENT
                    c.evidence.append("CI config + docs/pipeline.md with stage/gate structure")
                elif has_ci:
                    c.grade = Grade.PRESENT
                    c.evidence.append(f"Pipeline stages/gates in {len(stages)} files")
                else:
                    c.grade = Grade.PARTIAL
                    c.evidence.append("docs/pipeline.md contains stage/gate structure")
                    c.gaps.append("No GitHub Actions / GitLab CI / Jenkinsfile detected")
                    c.remediation = "Create .github/workflows/ci.yml with build/test/deploy stages matching docs/pipeline.md."
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("Pipeline files exist but no explicit stage/gate structure")
                c.remediation = "Add stage/gate documentation to docs/pipeline.md or README.md."
        else:
            c.gaps.append("No CI/CD pipeline, GitHub Actions, or deployment configuration")
            c.remediation = "Create .github/workflows/ci.yml and docs/pipeline.md with stage/gate structure."
        return c

    def audit_c4_oncall(self) -> Control:
        c = Control("C4.4", "On-Call Coverage", "C4")
        oc = self._find(["*oncall*", "*on-call*", "*pager*", "*rotation*", "*schedule*", "*escalation*", "*contact*", "*support*"])
        oc_files = [p for p in oc if p.suffix in [".md", ".yaml", ".yml", ".json", ".txt"]]
        rot = self._grep(["rotation", "primary", "secondary", "escalation", "on-call", "pager", "incident", "post-mortem", "SRE"], [".md", ".yaml", ".txt"])
        c.files_found = [str(p.relative_to(self.root)) for p in (oc_files + rot)[:20]]
        if oc_files:
            if rot:
                c.grade = Grade.PRESENT
                c.evidence.append(f"Rotation/escalation language in {len(rot)} files")
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("On-call docs lack rotation schedule or escalation SLA")
                c.remediation = "Add rotation schedule, primary/secondary assignments, and escalation SLA to existing on-call doc."
        else:
            c.gaps.append("No on-call coverage or escalation matrix found")
            c.remediation = "Create docs/oncall.md with rotation schedule, primary/secondary, escalation SLA, and handoff checklist."
        return c

    # ── C4+ ──────────────────────────────────────────────────────────────────
    def audit_c4p_adr_records(self) -> Control:
        c = Control("C4+.1", "ADR Records (Stakeholder Sign-off)", "C4+")
        adr = self._find(["*adr*", "*decision*"])
        adr_md = [p for p in adr if p.suffix == ".md"]
        c.files_found = [str(p.relative_to(self.root)) for p in adr_md[:20]]
        if adr_md:
            signoff = self._grep(["sign-off", "signoff", "approved by", "reviewed by", "stakeholder", "board", "consent", "dissent", "veto"], [".md"])
            if signoff:
                c.grade = Grade.PRESENT
                c.evidence.append(f"Stakeholder sign-off language in {len(signoff)} files")
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("ADRs lack stakeholder sign-off or dissent records")
                c.remediation = "Add stakeholder sign-off table to each ADR (Architecture Lead, Security Lead, SRE Lead)."
        else:
            c.gaps.append("No ADR files for stakeholder alignment")
            c.remediation = "Create docs/adr/ with ADR template including stakeholder sign-off table."
        return c

    def audit_c4p_review_board(self) -> Control:
        c = Control("C4+.2", "Review Board Sign-Up", "C4+")
        board = self._find(["*review*board*", "*board*", "*charter*", "*governance*", "*committee*", "*council*"])
        board_files = [p for p in board if p.suffix in [".md", ".txt", ".yaml", ".json"]]
        charter = self._grep(["charter", "quorum", "membership", "member", "chair", "meeting", "cadence", "review board", "architecture board"], [".md", ".txt"])
        c.files_found = [str(p.relative_to(self.root)) for p in (board_files + charter)[:20]]
        if board_files:
            if charter:
                c.grade = Grade.PRESENT
                c.evidence.append(f"Board charter/quorum in {len(charter)} files")
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("No explicit review board charter or quorum rules")
                c.remediation = "Add charter, quorum rules, and membership list to existing governance doc."
        else:
            c.gaps.append("No review board or governance committee documentation")
            c.remediation = "Create docs/governance/charter.md with review board charter, quorum rules, and membership."
        return c

    def audit_c4p_feedback_loops(self) -> Control:
        c = Control("C4+.3", "Feedback Loops", "C4+")
        fb = self._find(["*feedback*", "*user*feedback*", "*nps*", "*csat*", "*survey*", "*retrospective*", "*retro*", "*lessons*learned*"])
        fb_files = [p for p in fb if p.suffix in [".md", ".txt", ".yaml", ".json"]]
        loops = self._grep(["feedback", "NPS", "CSAT", "survey", "retrospective", "retro", "lessons learned", "incident to feature", "feature request"], [".md", ".txt"])
        c.files_found = [str(p.relative_to(self.root)) for p in (fb_files + loops)[:20]]
        if fb_files:
            if loops:
                c.grade = Grade.PRESENT
                c.evidence.append(f"Feedback loop mechanisms in {len(loops)} files")
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("No explicit feedback channels or NPS/CSAT")
                c.remediation = "Add user feedback channel, NPS/CSAT mechanism, and retrospective cadence to existing feedback doc."
        else:
            c.gaps.append("No feedback loop or retrospective documentation")
            c.remediation = "Create docs/feedback.md with user feedback channel, retro cadence, and incident-to-feature pipeline."
        return c

    def audit_c4p_fitness_functions(self) -> Control:
        c = Control("C4+.4", "Fitness Functions", "C4+")
        ff = self._find(["*fitness*", "*quality*gate*", "*architectural*drift*", "*compliance*test*", "*regression*", "*gate*"])
        ff_files = [p for p in ff if p.suffix in [".py", ".md", ".yaml", ".sh", ".txt"]]
        gates = self._grep(["fitness function", "quality gate", "architectural drift", "compliance test", "regression test", "gate", "threshold", "enforce", "invariant", "architecture test", "test_arch"], [".py", ".md", ".yaml"])
        c.files_found = [str(p.relative_to(self.root)) for p in (ff_files + gates)[:20]]
        if ff_files:
            if gates:
                c.grade = Grade.PRESENT
                c.evidence.append(f"Automated fitness functions in {len(gates)} files")
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("No automated architectural drift or compliance regression tests")
                c.remediation = "Add automated architecture drift detection to the CI pipeline."
        else:
            c.gaps.append("No fitness functions, quality gates, or automated compliance checks")
            c.remediation = "Create tests/test_architecture_drift.py with automated fitness functions for architecture purity."
        return c

    # ── C5 — Data Governance ─────────────────────────────────────────────────
    def audit_c5_data_governance(self) -> Control:
        c = Control("C5.1", "Data Governance (GDPR/HIPAA/Retention/PII)", "C5")
        dg = self._find(["*gdpr*", "*hipaa*", "*retention*", "*anonym*", "*pii*", "*privacy*", "*data*govern*", "*consent*", "*data*subject*"])
        dg_files = [p for p in dg if p.suffix in [".md", ".py", ".yaml", ".yml", ".json", ".txt"]]
        redaction = self._grep(["redaction", "anonymization", "pseudonymization", "retention", "gdpr", "hipaa", "consent", "right to erasure", "data subject", "pii", "phi"], [".py", ".md", ".yaml"])
        c.files_found = [str(p.relative_to(self.root)) for p in (dg_files + redaction)[:20]]
        if dg_files:
            if redaction:
                c.grade = Grade.PRESENT
                c.evidence.append(f"Data governance controls in {len(redaction)} files")
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("Data governance docs exist but no implementation detected")
                c.remediation = "Add retention policies, anonymization, and consent management to core config or ingestion pipeline."
        else:
            c.gaps.append("No data governance, privacy, or retention documentation")
            c.remediation = "Create docs/data_governance.md with GDPR/HIPAA compliance matrix, retention schedule, and anonymization policy."
        return c

    # ── C6 — Compliance/Regulatory ───────────────────────────────────────────
    def audit_c6_compliance(self) -> Control:
        c = Control("C6.1", "Compliance & Regulatory (Legal Hold / Audit Trail / Chain of Custody)", "C6")
        comp = self._find(["*compliance*", "*legal*hold*", "*audit*trail*", "*chain*custody*", "*regulatory*", "*litigation*"])
        comp_files = [p for p in comp if p.suffix in [".md", ".py", ".yaml", ".yml", ".json", ".txt"]]
        hold = self._grep(["legal hold", "litigation hold", "audit trail", "chain of custody", "tamper", "evidence", "forensic", "retention", "warrant", "subpoena", "audit log"], [".py", ".md", ".yaml"])
        c.files_found = [str(p.relative_to(self.root)) for p in (comp_files + hold)[:20]]
        if comp_files:
            if hold:
                c.grade = Grade.PRESENT
                c.evidence.append(f"Compliance controls in {len(hold)} files")
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("Compliance docs exist but no enforcement mechanism")
                c.remediation = "Add automated legal hold triggers and audit trail verification to litigation hold workflow."
        else:
            c.gaps.append("No compliance, legal hold, or audit trail documentation")
            c.remediation = "Create docs/compliance.md with legal hold procedures, audit trail requirements, and chain-of-custody protocol."
        return c

    # ── C7 — Disaster Recovery ───────────────────────────────────────────────
    def audit_c7_disaster_recovery(self) -> Control:
        c = Control("C7.1", "Disaster Recovery (RPO / RTO / Backup / Failover)", "C7")
        dr = self._find(["*disaster*", "*recovery*", "*backup*", "*failover*", "*rpo*", "*rto*", "*region*", "*replication*"])
        dr_files = [p for p in dr if p.suffix in [".md", ".py", ".yaml", ".yml", ".json", ".txt", ".sh"]]
        backup = self._grep(["backup", "restore", "rpo", "rto", "failover", "replication", "drill", "disaster recovery", "business continuity"], [".py", ".md", ".yaml", ".sh"])
        c.files_found = [str(p.relative_to(self.root)) for p in (dr_files + backup)[:20]]
        if dr_files:
            if backup:
                c.grade = Grade.PRESENT
                c.evidence.append(f"DR controls in {len(backup)} files")
            else:
                c.grade = Grade.PARTIAL
                c.gaps.append("DR docs exist but no backup/restore implementation")
                c.remediation = "Add automated backup verification and restore testing to hardening/backup scripts."
        else:
            c.gaps.append("No disaster recovery, backup, or failover documentation")
            c.remediation = "Create docs/disaster_recovery.md with RPO/RTO targets, backup schedule, and region failover procedure."
        return c

    # ── RUN ──────────────────────────────────────────────────────────────────
    def run(self) -> AuditReport:
        report = AuditReport(project_root=str(self.root))
        controls = [
            self.audit_c1_context_diagram(),
            self.audit_c1_api_contract(),
            self.audit_c1_decision_records(),
            self.audit_c2_runbook(),
            self.audit_c2_performance_budget(),
            self.audit_c2_security_model(),
            self.audit_c2_resilience_pattern(),
            self.audit_c2_scalability(),
            self.audit_c3_use_cases(),
            self.audit_c3_error_handling(),
            self.audit_c3_sequence_diagrams(),
            self.audit_c4_monitoring(),
            self.audit_c4_rollback(),
            self.audit_c4_deployment_pipeline(),
            self.audit_c4_oncall(),
            self.audit_c4p_adr_records(),
            self.audit_c4p_review_board(),
            self.audit_c4p_feedback_loops(),
            self.audit_c4p_fitness_functions(),
            self.audit_c5_data_governance(),
            self.audit_c6_compliance(),
            self.audit_c7_disaster_recovery(),
        ]
        report.controls = controls
        grade_counts = {g.value: 0 for g in Grade}
        for c in controls:
            grade_counts[c.grade.value] += 1
        report.summary = {
            "total_controls": len(controls),
            "grade_distribution": grade_counts,
            "present_rate": f"{grade_counts['PRESENT']}/{len(controls)}",
            "partial_rate": f"{grade_counts['PARTIAL']}/{len(controls)}",
            "missing_rate": f"{grade_counts['MISSING']}/{len(controls)}",
            **{f"{cat.lower()}_present": sum(1 for c in controls if c.category == cat and c.grade == Grade.PRESENT) for cat in ["C1", "C2", "C3", "C4", "C4+", "C5", "C6", "C7"]},
            **{f"{cat.lower()}_total": sum(1 for c in controls if c.category == cat) for cat in ["C1", "C2", "C3", "C4", "C4+", "C5", "C6", "C7"]},
        }
        return report


# ── Utilities ──────────────────────────────────────────────────────────────

def load_previous_report(project_root: str) -> Optional[Dict]:
    prev_path = Path(project_root) / "structural_audit_report.json"
    if prev_path.exists():
        try:
            with open(prev_path) as f:
                data = json.load(f)
                return data.get("summary", {})
        except Exception:
            pass
    return None


def compute_delta(current: Dict, previous: Optional[Dict]) -> Dict:
    if not previous:
        return {"status": "first_run", "changes": []}
    changes = []
    for key in ["present_rate", "partial_rate", "missing_rate"]:
        curr = current.get(key, "0/0")
        prev = previous.get(key, "0/0")
        if curr != prev:
            changes.append(f"{key}: {prev} -> {curr}")
    for cat in ["c1", "c2", "c3", "c4", "c4plus", "c5", "c6", "c7"]:
        curr_p = current.get(f"{cat}_present", 0)
        prev_p = previous.get(f"{cat}_present", 0)
        curr_t = current.get(f"{cat}_total", 0)
        prev_t = previous.get(f"{cat}_total", 0)
        if curr_p != prev_p or curr_t != prev_t:
            changes.append(f"{cat.upper()}: {prev_p}/{prev_t} -> {curr_p}/{curr_t} PRESENT")
    return {
        "status": "delta_computed",
        "changes": changes,
        "improved": sum(1 for ch in changes if "PRESENT" in ch and ch.split("->")[0].split("/")[0].strip() < ch.split("->")[1].split("/")[0].strip())
    }


def save_history(report: AuditReport):
    root = Path(report.project_root)
    history_file = root / ".audit_history.json"
    history = []
    if history_file.exists():
        try:
            with open(history_file) as f:
                history = json.load(f)
        except Exception:
            history = []
    present = int(report.summary['present_rate'].split('/')[0])
    total = report.summary['total_controls']
    history.append({
        "date": datetime.now().isoformat(),
        "present": present,
        "partial": int(report.summary['partial_rate'].split('/')[0]),
        "missing": int(report.summary['missing_rate'].split('/')[0]),
        "total": total
    })
    with open(history_file, "w") as f:
        json.dump(history, f, indent=2)


def generate_trend_graph(project_root: str) -> bool:
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed. Install with: pip install matplotlib")
        return False
    root = Path(project_root)
    history_file = root / ".audit_history.json"
    if not history_file.exists():
        print("No audit history found. Run audit multiple times to build trend.")
        return False
    with open(history_file) as f:
        history = json.load(f)
    if len(history) < 2:
        print("Need at least 2 runs for trend graph.")
        return False
    dates = [h["date"][:10] for h in history]
    present_rates = [h["present"] / h["total"] * 100 for h in history]
    partial_rates = [h["partial"] / h["total"] * 100 for h in history]
    missing_rates = [h["missing"] / h["total"] * 100 for h in history]

    plt.figure(figsize=(10, 6))
    plt.plot(dates, present_rates, marker='o', linewidth=2, markersize=8, label='PRESENT')
    plt.plot(dates, partial_rates, marker='s', linewidth=2, markersize=6, label='PARTIAL')
    plt.plot(dates, missing_rates, marker='^', linewidth=2, markersize=6, label='MISSING')
    plt.axhline(y=100, color='g', linestyle='--', alpha=0.5, label='100% Target')
    plt.fill_between(dates, present_rates, alpha=0.2)
    plt.xlabel('Date')
    plt.ylabel('Rate (%)')
    plt.title('Structural Completeness Trend')
    plt.xticks(rotation=45)
    plt.ylim(0, 105)
    plt.legend()
    plt.tight_layout()
    output_path = root / "audit_trend.png"
    plt.savefig(output_path, dpi=150)
    print(f"Trend graph saved to: {output_path}")
    return True


def print_report(report: AuditReport):
    print("=" * 70)
    print("STRUCTURAL COMPLETENESS AUDIT")
    print(f"Project Root: {report.project_root}")
    print(f"Total Controls: {report.summary['total_controls']}")
    print(f"Scanned files: {len([p for p in Path(report.project_root).rglob('*') if p.is_file() and not any(part in EXCLUDED_DIRS for part in p.relative_to(Path(report.project_root)).parts)])}")
    if report.delta and report.delta.get("status") == "delta_computed":
        print(f"\nDELTA FROM PREVIOUS RUN")
        for change in report.delta.get("changes", []):
            print(f"  {change}")
    elif report.delta and report.delta.get("status") == "first_run":
        print(f"\nFIRST RUN — no previous baseline")
    print("=" * 70)
    print()

    for cat in ["C1", "C2", "C3", "C4", "C4+", "C5", "C6", "C7"]:
        cat_controls = [c for c in report.controls if c.category == cat]
        present = sum(1 for c in cat_controls if c.grade == Grade.PRESENT)
        print(f"\n{'─' * 70}")
        print(f"  {cat} — {present}/{len(cat_controls)} PRESENT")
        print(f"{'─' * 70}")
        for c in cat_controls:
            status = f"[{c.grade.value:8s}]"
            print(f"\n  {status} {c.id} {c.name}")
            if c.evidence:
                for e in c.evidence:
                    print(f"      + {e}")
            if c.gaps:
                for g in c.gaps:
                    print(f"      - {g}")
            if c.remediation:
                print(f"      -> REMEDIATION: {c.remediation}")
            if c.files_found:
                print(f"      -> Files ({len(c.files_found)}):")
                for f in c.files_found[:5]:
                    print(f"         . {f}")
                if len(c.files_found) > 5:
                    print(f"         . ... and {len(c.files_found) - 5} more")

    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    print(f"  PRESENT : {report.summary['present_rate']}")
    print(f"  PARTIAL : {report.summary['partial_rate']}")
    print(f"  MISSING : {report.summary['missing_rate']}")
    print(f"{'=' * 70}")
    total = report.summary['total_controls']
    present = int(report.summary['present_rate'].split('/')[0])
    if present == total:
        print(f"\nCI GATE: PASS — {present}/{total} PRESENT")
    else:
        print(f"\nCI GATE: FAIL — {present}/{total} PRESENT (need {total}/{total})")


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    root = args[0]
    ci_mode = "--ci" in args
    trend_mode = "--trend" in args

    if trend_mode:
        return 0 if generate_trend_graph(root) else 1

    auditor = StructuralAuditEngine(root)
    report = auditor.run()
    report.previous_summary = load_previous_report(root)
    report.delta = compute_delta(report.summary, report.previous_summary)
    print_report(report)
    save_history(report)

    json_path = Path(root) / "structural_audit_report.json"
    with open(json_path, "w") as f:
        json.dump(report.to_dict(), f, indent=2)
    print(f"\nJSON report written to: {json_path.absolute()}")
    print(f"History appended to: {Path(root) / '.audit_history.json'}")

    if ci_mode:
        total = report.summary['total_controls']
        present = int(report.summary['present_rate'].split('/')[0])
        return 0 if present == total else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
