"""Pydantic models for the Reproducible Fusion Engine."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Decay(BaseModel):
    """Per-stream decay specification."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["exponential", "unbounded"]
    half_life_hours: float | None = Field(default=None, ge=0.0)
    justification: str | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_half_life(self) -> Decay:
        if self.method == "exponential" and (
            self.half_life_hours is None or self.half_life_hours <= 0
        ):
            raise ValueError("exponential decay requires a positive half_life_hours")
        if self.method == "unbounded" and self.half_life_hours is not None:
            raise ValueError("unbounded decay must not specify half_life_hours")
        return self


class Stream(BaseModel):
    """A single evidence stream in an RFE manifest."""

    model_config = ConfigDict(extra="allow")

    stream_id: str
    type: str
    source_tier: int = Field(..., ge=1, le=5)
    content: dict[str, Any]
    confidence: float = Field(..., ge=0.0, le=1.0)
    provenance_hash: str
    signature: str | None = Field(default=None)
    timestamp: str
    decay: Decay | None = Field(default=None)
    severity_impact: float = Field(default=0.5, ge=0.0, le=1.0)
    relevance_tags: list[str] = Field(default_factory=list)

    @field_validator("timestamp")
    @classmethod
    def _iso_timestamp(cls, value: str) -> str:
        # Validate and normalize to a consistent string representation.
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.isoformat(timespec="seconds")


class Constraints(BaseModel):
    """Output constraints for the generated report."""

    model_config = ConfigDict(extra="forbid")

    max_pages: int = Field(default=20, ge=1)
    required_sections: list[str] = Field(default_factory=list)
    output_format: Literal["pdf-a-1b", "json", "markdown"] = Field(default="pdf-a-1b")
    language: str = Field(default="en")


class Manifest(BaseModel):
    """RFE input manifest."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    timestamp: str
    streams: list[Stream]
    constraints: Constraints = Field(default_factory=Constraints)

    @field_validator("timestamp")
    @classmethod
    def _iso_timestamp(cls, value: str) -> str:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.isoformat(timespec="seconds")


class AuthorityAssessment(BaseModel):
    """Authority assessment for a stream after signature/tier checks."""

    stream_id: str
    declared_tier: int
    effective_tier: int
    authority_weight: float
    signature_valid: bool | None
    signature_status: str
    anomalies: list[str] = Field(default_factory=list)


class ScoredStream(BaseModel):
    """A stream enriched with computed scores."""

    stream_id: str
    stream_type: str
    source_tier: int
    authority_weight: float
    confidence: float
    severity_impact: float
    freshness: float
    corroboration_count: int
    composite_score: float
    decayed_impact: float
    decay_method: str
    half_life_hours: float | None
    flags: list[str] = Field(default_factory=list)


class ConflictResolution(BaseModel):
    """Outcome of arbitration between conflicting streams."""

    conflict_id: str
    stream_ids: list[str]
    resolution_rule: str
    resolved: bool
    winning_stream_id: str | None
    loser_stream_ids: list[str] = Field(default_factory=list)
    dispute_forced: bool
    score_gap: float
    rationale: str


class SensitivityScenario(BaseModel):
    """One sensitivity scenario: half-life varied by ±X%."""

    variation_label: str
    variation_factor: float
    conclusions: list[str]
    flipped_conclusions: list[str] = Field(default_factory=list)
    scored_streams: list[dict[str, Any]] = Field(default_factory=list)


class SensitivityAppendix(BaseModel):
    """Sensitivity analysis appendix for finite-decay streams."""

    enabled: bool
    variation_factor: float
    baseline_conclusions: list[str]
    scenarios: list[SensitivityScenario]
    flipped_conclusions: list[str]
    methodology: str


class DecisionLog(BaseModel):
    """Auditable decision log produced by the RFE."""

    engine_version: str
    policy_version: str
    reasoning_version_id: str
    case_id: str
    manifest_timestamp: str
    streams_received: int
    streams_accepted: int
    streams_rejected: int
    authority_assessments: list[AuthorityAssessment]
    scored_streams: list[ScoredStream]
    conflicts: list[ConflictResolution]
    sensitivity_appendix: SensitivityAppendix | None
    baseline_conclusions: list[str] = Field(default_factory=list)
    anomalies: list[str]
    arbitration_threshold: float
    dead_band: float
    scoring_weights: dict[str, float]


class ReportSection(BaseModel):
    """A rendered report section."""

    name: str
    title: str
    rendered: str
    source_stream_ids: list[str] = Field(default_factory=list)


class Report(BaseModel):
    """Final RFE report structure."""

    case_id: str
    generated_at: str
    engine_version: str
    policy_version: str
    reasoning_version_id: str
    language: str
    sections: list[ReportSection]
    decision_log: DecisionLog
    report_hash: str
    decision_log_hash: str
    version_id: str


class FeedbackRequest(BaseModel):
    """Feedback ingestion request."""

    case_id: str
    stream_id: str | None = Field(default=None)
    type: str = Field(default="human_feedback")
    source_tier: int = Field(default=2, ge=1, le=5)
    content: dict[str, Any]
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    severity_impact: float = Field(default=0.5, ge=0.0, le=1.0)
    relevance_tags: list[str] = Field(default_factory=list)
    provenance_hash: str | None = Field(default=None)
    signature: str | None = Field(default=None)
    timestamp: str | None = Field(default=None)
    decay: Decay | None = Field(default=None)

    @field_validator("timestamp")
    @classmethod
    def _iso_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.isoformat(timespec="seconds")


class FeedbackResponse(BaseModel):
    """Feedback ingestion response containing the new stream object."""

    status: str
    stream: dict[str, Any]
    message: str


class VersionInfo(BaseModel):
    """A past RFE report version entry."""

    version_id: str
    case_id: str
    report_hash: str
    timestamp_ns: int
    event: str


class GenerateResponse(BaseModel):
    """Response from POST /api/v1/rfe/generate."""

    status: str
    report: Report
    report_hash: str
    decision_log_hash: str
    version_id: str


class ConfigResponse(BaseModel):
    """Response from GET /api/v1/rfe/config."""

    status: str
    config: dict[str, Any]


class HealthResponse(BaseModel):
    """Response from GET /api/v1/rfe/health."""

    status: str
    engine_version: str
    policy_version: str


class VersionsResponse(BaseModel):
    """Response from GET /api/v1/rfe/versions."""

    status: str
    versions: list[VersionInfo]
