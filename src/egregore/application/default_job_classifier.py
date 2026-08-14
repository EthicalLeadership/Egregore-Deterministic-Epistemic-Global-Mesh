"""Default deterministic job classifier.

Maps a JobRequest to JobClassification using only audited fields:
- WorkUnitType from payload metadata.
- ComplexityTier from a fixed mapping.
- ResourceProfile from payload metadata or safe defaults.
"""

from __future__ import annotations

from egregore.domain.job_models import (
    ComplexityTier,
    JobClassification,
    JobRequest,
    ResourceProfile,
)
from egregore.domain.work_unit import WorkUnitType
from egregore.interface.job_router_ports import IJobClassifier


class DefaultJobClassifier:
    """Classify JobRequest using deterministic work-unit metadata."""

    COMPLEXITY_BY_WORK_UNIT_TYPE = {
        WorkUnitType.LLM_INFERENCE: ComplexityTier.STANDARD,
        WorkUnitType.TENSOR_OPERATION: ComplexityTier.STANDARD,
        WorkUnitType.FEATURE_ENGINEERING: ComplexityTier.TRIVIAL,
        WorkUnitType.IMAP_INGESTION: ComplexityTier.TRIVIAL,
        WorkUnitType.DATABASE_QUERY: ComplexityTier.TRIVIAL,
        WorkUnitType.FILE_SYSTEM_SCAN: ComplexityTier.TRIVIAL,
        WorkUnitType.HYBRID_AI_AGENT: ComplexityTier.COMPLEX,
        WorkUnitType.DATA_TURBINE_STREAM: ComplexityTier.STANDARD,
        WorkUnitType.GOVERNANCE_AUDIT: ComplexityTier.COMPLEX,
        WorkUnitType.PROVENANCE_COMPACTION: ComplexityTier.STANDARD,
    }

    def classify(self, request: JobRequest) -> JobClassification:
        wu_type_name = request.payload.get("_work_unit_type")
        try:
            wu_type = WorkUnitType[wu_type_name] if wu_type_name else None
        except KeyError:
            wu_type = None

        complexity = self.COMPLEXITY_BY_WORK_UNIT_TYPE.get(
            wu_type, ComplexityTier.STANDARD
        )

        rp = request.payload.get("resource_profile")
        if isinstance(rp, dict):
            resource_profile = ResourceProfile(
                cpu_percent=float(rp.get("cpu_percent", 0.0)),
                memory_mb=int(rp.get("memory_mb", 0)),
                vram_mb=int(rp.get("vram_mb", 0)),
                disk_iops=int(rp.get("disk_iops", 0)),
                network_mbps=int(rp.get("network_mbps", 0)),
            )
        else:
            resource_profile = ResourceProfile()

        priority_tier = request.priority_hint or "STANDARD"
        created_at_ns = request.metadata.get("timestamp_ns", 0)

        return JobClassification(
            job_id=request.job_id,
            complexity=complexity,
            resource_profile=resource_profile,
            estimated_tokens=int(request.payload.get("estimated_tokens", 0)),
            target_vertical=request.payload.get("target_vertical", ""),
            requested_capabilities=request.requested_capabilities,
            deterministic_required=request.payload.get(
                "deterministic_required", False
            ),
            priority_tier=priority_tier,
            created_at_ns=created_at_ns,
        )
