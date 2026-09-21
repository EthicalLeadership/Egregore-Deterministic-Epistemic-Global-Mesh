"""Core Reproducible Fusion Engine: pure deterministic fusion function."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from egregore.rfe.arbitration import ArbitrationEngine
from egregore.rfe.config import RFEConfig, load_rfe_config
from egregore.rfe.models import (
    DecisionLog,
    FeedbackRequest,
    Manifest,
    Report,
    ReportSection,
    ScoredStream,
    SensitivityAppendix,
    SensitivityScenario,
    Stream,
)
from egregore.rfe.security import default_security_analyzer
from egregore.tooling.deterministic_verification import (
    DeterministicVerifier,
    canonical_dumps,
)

__all__ = [
    "reproducible_fusion",
    "feedback_to_stream",
    "manifest_fingerprint",
    "MAX_CORROBORATION_REFERENCE",
]


MAX_CORROBORATION_REFERENCE = 10


def _parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _claim_flag(stream: Stream) -> str:
    claim = stream.content.get("claim", "neutral")
    if claim == "positive":
        return "claim:positive"
    if claim == "negative":
        return "claim:negative"
    return "claim:neutral"


def _subject(stream: Stream) -> str:
    return str(stream.content.get("subject", stream.stream_id))


def _content_text(stream: Stream) -> str:
    content = dict(stream.content)
    content.pop("claim", None)
    content.pop("subject", None)
    if "text" in content:
        return str(content["text"])
    if "statement" in content:
        return str(content["statement"])
    if content:
        return canonical_dumps(content)
    return "(no text content)"


class _FusionContext:
    def __init__(self, manifest: Manifest, config: RFEConfig) -> None:
        self.manifest = manifest
        self.config = config
        self.weights = config.scoring_weights
        self.weight_sum = (
            self.weights["w_impact"]
            + self.weights["w_freshness"]
            + self.weights["w_reliability"]
            + self.weights["w_corroboration"]
        )
        self.manifest_dt = _parse_ts(manifest.timestamp)
        self.security = default_security_analyzer(
            config, verify_keys=config.verify_keys
        )

    def _decay_for_stream(self, stream: Stream) -> tuple[str, float | None, list[str]]:
        if stream.decay is None:
            return "unbounded", None, ["decay_method:unbounded"]
        if stream.decay.method == "unbounded":
            return "unbounded", None, []
        return "exponential", stream.decay.half_life_hours, []

    def _freshness(self, stream: Stream, method: str, half_life: float | None) -> float:
        if method == "unbounded" or half_life is None or half_life <= 0:
            return 1.0
        stream_dt = _parse_ts(stream.timestamp)
        age_hours = max(0.0, (self.manifest_dt - stream_dt).total_seconds() / 3600.0)
        return float(0.5 ** (age_hours / half_life))

    def _corroboration(self, stream: Stream, all_streams: list[Stream]) -> int:
        own_flag = _claim_flag(stream)
        own_subject = _subject(stream)
        if own_flag == "claim:neutral":
            return 0
        count = 0
        for other in all_streams:
            if other.stream_id == stream.stream_id:
                continue
            if _claim_flag(other) == own_flag and _subject(other) == own_subject:
                count += 1
        return count

    def _normalized_corroboration(self, count: int) -> float:
        return min(count, MAX_CORROBORATION_REFERENCE) / MAX_CORROBORATION_REFERENCE

    def _composite_score(
        self,
        stream: Stream,
        authority_weight: float,
        freshness: float,
        corroboration_count: int,
    ) -> float:
        impact = stream.severity_impact
        reliability = stream.confidence
        corroboration = self._normalized_corroboration(corroboration_count)
        weighted = (
            self.weights["w_impact"] * impact
            + self.weights["w_freshness"] * freshness
            + self.weights["w_reliability"] * reliability
            + self.weights["w_corroboration"] * corroboration
        ) / self.weight_sum
        return authority_weight * weighted

    def _score_stream(
        self,
        stream: Stream,
        assessment: Any,
        all_streams: list[Stream],
    ) -> ScoredStream:
        method, half_life, decay_flags = self._decay_for_stream(stream)
        freshness = self._freshness(stream, method, half_life)
        corroboration_count = self._corroboration(stream, all_streams)
        composite = self._composite_score(
            stream,
            authority_weight=assessment.authority_weight,
            freshness=freshness,
            corroboration_count=corroboration_count,
        )
        flags = list(decay_flags)
        flags.append(_claim_flag(stream))
        if assessment.signature_valid is True:
            flags.append("signature_valid")
        elif assessment.signature_valid is False:
            flags.append("signature_invalid")
        else:
            flags.append("unsigned")
        if assessment.anomalies:
            flags.extend(assessment.anomalies)
        return ScoredStream(
            stream_id=stream.stream_id,
            stream_type=stream.type,
            source_tier=stream.source_tier,
            authority_weight=assessment.authority_weight,
            confidence=stream.confidence,
            severity_impact=stream.severity_impact,
            freshness=freshness,
            corroboration_count=corroboration_count,
            composite_score=composite,
            decayed_impact=stream.severity_impact * freshness,
            decay_method=method,
            half_life_hours=half_life,
            flags=sorted(set(flags)),
        )

    def _conclusions(
        self, scored_streams: list[ScoredStream]
    ) -> tuple[list[str], list[str]]:
        supported: list[str] = []
        opposed: list[str] = []
        for s in scored_streams:
            if s.composite_score < self.config.min_confidence:
                continue
            subject = None
            for raw in self.manifest.streams:
                if raw.stream_id == s.stream_id:
                    subject = _subject(raw)
                    break
            if subject is None:
                subject = s.stream_id
            if "claim:positive" in s.flags:
                supported.append(f"{subject}: supported")
            elif "claim:negative" in s.flags:
                opposed.append(f"{subject}: opposed")
        # Deduplicate while preserving order.
        return list(dict.fromkeys(supported)), list(dict.fromkeys(opposed))

    def _render_template(self, name: str, context: dict[str, Any]) -> str:
        env = Environment(
            loader=PackageLoader("egregore.rfe", "templates"),
            autoescape=select_autoescape(["html", "xml"]),
        )
        template = env.get_template(name)
        return template.render(context)

    def _sensitivity_appendix(
        self,
        scored_streams: list[ScoredStream],
    ) -> SensitivityAppendix | None:
        finite_streams = [
            (idx, s)
            for idx, s in enumerate(scored_streams)
            if s.decay_method == "exponential"
        ]
        if not finite_streams:
            return None

        variation = self.config.sensitivity_variation
        baseline_supported, baseline_opposed = self._conclusions(scored_streams)
        baseline = baseline_supported + baseline_opposed

        scenarios: list[SensitivityScenario] = []
        all_flipped: set[str] = set()

        for label, factor in [
            ("half_life_plus_50", 1.0 + variation),
            ("half_life_minus_50", 1.0 - variation),
        ]:
            mutated_scores = list(scored_streams)
            for idx, s in finite_streams:
                new_half_life = (s.half_life_hours or 1.0) * factor
                # Find the original stream to recompute.
                original = next(
                    st for st in self.manifest.streams if st.stream_id == s.stream_id
                )
                new_freshness = self._freshness(original, "exponential", new_half_life)
                new_score = self._composite_score(
                    original,
                    authority_weight=s.authority_weight,
                    freshness=new_freshness,
                    corroboration_count=s.corroboration_count,
                )
                mutated_scores[idx] = s.model_copy(
                    update={
                        "freshness": new_freshness,
                        "composite_score": new_score,
                        "half_life_hours": new_half_life,
                    }
                )
            sup, opp = self._conclusions(mutated_scores)
            scenario_conclusions = sup + opp
            flipped = set(scenario_conclusions) ^ set(baseline)
            all_flipped.update(flipped)
            scenarios.append(
                SensitivityScenario(
                    variation_label=label,
                    variation_factor=factor,
                    conclusions=scenario_conclusions,
                    flipped_conclusions=sorted(flipped),
                    scored_streams=[m.model_dump() for m in mutated_scores],
                )
            )

        return SensitivityAppendix(
            enabled=True,
            variation_factor=variation,
            baseline_conclusions=baseline,
            scenarios=scenarios,
            flipped_conclusions=sorted(all_flipped),
            methodology=(
                "For each finite-decay stream, half-life is multiplied by "
                f"(1 ± {variation}) and MEEV scores/conclusions are recomputed."
            ),
        )

    def _render_report(
        self,
        scored_streams: list[ScoredStream],
        conflicts: list[Any],
        sensitivity: SensitivityAppendix | None,
        anomalies: list[str],
        supported: list[str],
        opposed: list[str],
        decision_log: DecisionLog,
        report_hash: str,
        decision_log_hash: str,
    ) -> Report:
        sorted_streams = sorted(
            scored_streams, key=lambda s: s.composite_score, reverse=True
        )
        top_n = sorted_streams[: self.config.max_streams_per_section]

        # Timeline entries: streams sorted by timestamp, then score.
        timeline_entries = []
        for raw in sorted(
            self.manifest.streams,
            key=lambda st: (_parse_ts(st.timestamp), st.stream_id),
        ):
            scored = next(
                (s for s in scored_streams if s.stream_id == raw.stream_id), None
            )
            claim = raw.content.get("claim", "neutral")
            timeline_entries.append(
                {
                    "timestamp": raw.timestamp,
                    "stream_id": raw.stream_id,
                    "stream_type": raw.type,
                    "composite_score": scored.composite_score if scored else 0.0,
                    "claim": claim,
                    "content_text": _content_text(raw),
                }
            )

        # Trajectory notes: no prediction disclaimer.
        trajectory_notes = []
        for s in sorted_streams[:5]:
            if s.composite_score < self.config.min_confidence:
                label = "low confidence"
                interval = "qualitative: unreliable"
            elif s.composite_score >= 0.85:
                label = "high confidence"
                interval = "qualitative: strong"
            elif s.composite_score >= 0.65:
                label = "moderate confidence"
                interval = "qualitative: tentative"
            else:
                label = "weak confidence"
                interval = "qualitative: uncertain"
            trajectory_notes.append(
                {
                    "stream_id": s.stream_id,
                    "label": label,
                    "interval": interval,
                    "disclaimer": (
                        "This is a retrospective assessment only; it does not predict future "
                        "events and must not be used for extrapolation."
                    ),
                }
            )

        disputes = [c for c in conflicts if not c.resolved or c.dispute_forced]
        overruled_ids = set()
        for c in conflicts:
            if c.resolved and c.loser_stream_ids:
                overruled_ids.update(c.loser_stream_ids)
        overruled_streams = [s for s in scored_streams if s.stream_id in overruled_ids]

        perspectives = []
        for s in top_n:
            raw = next(
                st for st in self.manifest.streams if st.stream_id == s.stream_id
            )
            perspectives.append(
                {
                    "stream_id": s.stream_id,
                    "source_tier": s.source_tier,
                    "stream_type": s.stream_type,
                    "composite_score": s.composite_score,
                    "authority_weight": s.authority_weight,
                    "flags": s.flags,
                    "content_text": _content_text(raw),
                }
            )

        sections: list[ReportSection] = [
            ReportSection(
                name="summary",
                title="Summary",
                rendered=self._render_template(
                    "summary.md.j2",
                    {
                        "case_id": self.manifest.case_id,
                        "generated_at": self.manifest.timestamp,
                        "engine_version": self.config.engine_version,
                        "policy_version": self.config.policy_version,
                        "reasoning_version_id": self.config.reasoning_version_id,
                        "report_hash": report_hash,
                        "decision_log_hash": decision_log_hash,
                        "key_findings": supported[
                            : self.config.max_streams_per_section
                        ],
                        "scored_streams": [s.model_dump() for s in sorted_streams],
                        "min_confidence": self.config.min_confidence,
                        "anomalies": anomalies,
                    },
                ),
            ),
            ReportSection(
                name="timeline",
                title="Timeline",
                rendered=self._render_template(
                    "timeline.md.j2",
                    {
                        "timeline_entries": timeline_entries,
                    },
                ),
            ),
            ReportSection(
                name="analysis",
                title="Analysis",
                rendered=self._render_template(
                    "analysis.md.j2",
                    {
                        "scored_streams": [s.model_dump() for s in sorted_streams],
                        "trajectory_notes": trajectory_notes,
                    },
                ),
            ),
            ReportSection(
                name="conclusion",
                title="Conclusion",
                rendered=self._render_template(
                    "conclusion.md.j2",
                    {
                        "supported_conclusions": supported,
                        "opposed_conclusions": opposed,
                        "weights": self.weights,
                    },
                ),
            ),
            ReportSection(
                name="perspectives",
                title="Perspectives",
                rendered=self._render_template(
                    "perspectives.md.j2",
                    {
                        "perspectives": perspectives,
                    },
                ),
            ),
        ]

        if disputes:
            sections.append(
                ReportSection(
                    name="disputed",
                    title="Disputed Findings",
                    rendered=self._render_template(
                        "disputed.md.j2",
                        {
                            "disputes": [c.model_dump() for c in disputes],
                            "dead_band": self.config.dead_band,
                        },
                    ),
                )
            )

        if sensitivity:
            sections.append(
                ReportSection(
                    name="sensitivity_appendix",
                    title="Appendix A — Sensitivity Analysis",
                    rendered=self._render_template(
                        "sensitivity_appendix.md.j2",
                        {
                            "variation_factor": sensitivity.variation_factor,
                            "baseline_conclusions": sensitivity.baseline_conclusions,
                            "scenarios": [
                                s.model_dump() for s in sensitivity.scenarios
                            ],
                            "flipped_conclusions": sensitivity.flipped_conclusions,
                        },
                    ),
                )
            )

        if overruled_streams:
            sections.append(
                ReportSection(
                    name="overruled_evidence",
                    title="Overruled Evidence",
                    rendered=self._render_template(
                        "overruled_evidence.md.j2",
                        {
                            "overruled_streams": [
                                s.model_dump() for s in overruled_streams
                            ],
                        },
                    ),
                )
            )

        version_id = hashlib.sha256(
            f"{self.manifest.case_id}|{report_hash}|{self.config.engine_version}".encode()
        ).hexdigest()[:24]

        return Report(
            case_id=self.manifest.case_id,
            generated_at=self.manifest.timestamp,
            engine_version=self.config.engine_version,
            policy_version=self.config.policy_version,
            reasoning_version_id=self.config.reasoning_version_id,
            language=self.manifest.constraints.language,
            sections=sections,
            decision_log=decision_log,
            report_hash=report_hash,
            decision_log_hash=decision_log_hash,
            version_id=version_id,
        )


def reproducible_fusion(
    manifest: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure deterministic fusion function.

    Args:
        manifest: RFE input manifest (see spec).
        config: Versioned RFE configuration. If None, loads ``config/rfe_config.yaml``.

    Returns:
        A dict containing the report structure, decision log, hashes, and version
        metadata. The same manifest + config always produces byte-identical output.

    """
    parsed_manifest = Manifest.model_validate(manifest)
    cfg = RFEConfig(config) if config is not None else RFEConfig(load_rfe_config())
    ctx = _FusionContext(parsed_manifest, cfg)

    # Security and authority assessment.
    assessments, global_anomalies = ctx.security.assess_streams(parsed_manifest)
    assessment_by_id = {a.stream_id: a for a in assessments}

    # Score every stream.
    scored_streams = [
        ctx._score_stream(
            stream,
            assessment_by_id[stream.stream_id],
            parsed_manifest.streams,
        )
        for stream in parsed_manifest.streams
    ]

    # Arbitration.
    original_stream_data = {
        s.stream_id: s.model_dump() for s in parsed_manifest.streams
    }
    arb = ArbitrationEngine(
        arbitration_threshold=cfg.arbitration_threshold,
        dead_band=cfg.dead_band,
    )
    conflicts = arb.arbitrate(scored_streams, original_stream_data)

    # Conclusions.
    supported, opposed = ctx._conclusions(scored_streams)

    # Sensitivity appendix.
    sensitivity = ctx._sensitivity_appendix(scored_streams)

    # Build decision log.
    accepted = [s for s in scored_streams if s.composite_score >= cfg.min_confidence]
    rejected = [s for s in scored_streams if s.composite_score < cfg.min_confidence]
    decision_log = DecisionLog(
        engine_version=cfg.engine_version,
        policy_version=cfg.policy_version,
        reasoning_version_id=cfg.reasoning_version_id,
        case_id=parsed_manifest.case_id,
        manifest_timestamp=parsed_manifest.timestamp,
        streams_received=len(parsed_manifest.streams),
        streams_accepted=len(accepted),
        streams_rejected=len(rejected),
        authority_assessments=assessments,
        scored_streams=scored_streams,
        conflicts=conflicts,
        sensitivity_appendix=sensitivity,
        baseline_conclusions=supported + opposed,
        anomalies=global_anomalies,
        arbitration_threshold=cfg.arbitration_threshold,
        dead_band=cfg.dead_band,
        scoring_weights=cfg.scoring_weights,
    )

    # Deterministic hashes. The report hash is part of the report itself, so we
    # do a two-pass render: first with a placeholder, then with the real hash.
    verifier = DeterministicVerifier()
    decision_log_hash = verifier.hash_decision_log(decision_log.model_dump())

    report = ctx._render_report(
        scored_streams=scored_streams,
        conflicts=conflicts,
        sensitivity=sensitivity,
        anomalies=global_anomalies,
        supported=supported,
        opposed=opposed,
        decision_log=decision_log,
        report_hash="__PLACEHOLDER__",
        decision_log_hash=decision_log_hash,
    )
    report_hash = verifier.hash_report(report.model_dump())

    # Re-render with the correct report hash now that it is known.
    report = ctx._render_report(
        scored_streams=scored_streams,
        conflicts=conflicts,
        sensitivity=sensitivity,
        anomalies=global_anomalies,
        supported=supported,
        opposed=opposed,
        decision_log=decision_log,
        report_hash=report_hash,
        decision_log_hash=decision_log_hash,
    )

    return {
        "report": report.model_dump(),
        "report_hash": report_hash,
        "decision_log_hash": decision_log_hash,
        "version_id": report.version_id,
    }


def manifest_fingerprint(manifest: dict[str, Any]) -> str:
    """Return a deterministic SHA-256 fingerprint of a manifest."""
    from egregore.tooling.deterministic_verification import fingerprint_canonical

    return fingerprint_canonical(manifest)


def feedback_to_stream(feedback: FeedbackRequest) -> dict[str, Any]:
    """Convert a feedback request into a ``human_feedback`` stream object."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    stream: dict[str, Any] = {
        "stream_id": feedback.stream_id
        or f"human_feedback_{hashlib.sha256(canonical_dumps(feedback.model_dump()).encode()).hexdigest()[:16]}",
        "type": feedback.type,
        "source_tier": feedback.source_tier,
        "content": dict(feedback.content),
        "confidence": feedback.confidence,
        "provenance_hash": feedback.provenance_hash
        or hashlib.sha256(
            canonical_dumps(feedback.model_dump()).encode("utf-8")
        ).hexdigest(),
        "signature": feedback.signature,
        "timestamp": feedback.timestamp or now,
        "decay": None,
        "severity_impact": feedback.severity_impact,
        "relevance_tags": list(feedback.relevance_tags),
    }
    if feedback.decay is not None:
        stream["decay"] = feedback.decay.model_dump()
    return stream
