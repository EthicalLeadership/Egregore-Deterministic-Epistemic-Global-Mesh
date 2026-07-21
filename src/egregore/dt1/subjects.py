from __future__ import annotations

from dataclasses import dataclass

from egregore.dt1.models import Dt1Class, Priority


def dt1_class_token(dt1_class: Dt1Class) -> str:
    if dt1_class == Dt1Class.DT1_CLASS_L:
        return "L"
    if dt1_class == Dt1Class.DT1_CLASS_H:
        return "H"
    return "U"  # unspecified


def priority_token(priority: Priority) -> str:
    # Spec uses p0..p7
    return f"p{int(priority)}"


def workunit_subject(
    *, dt1_class: Dt1Class, dt1_type: str, priority: Priority, site: str
) -> str:
    return (
        f"dt1.{dt1_class_token(dt1_class)}.{dt1_type}.{priority_token(priority)}.{site}"
    )


def dlq_subject(
    *, dt1_class: Dt1Class, dt1_type: str, priority: Priority, site: str
) -> str:
    return f"dt1.dlq.{dt1_class_token(dt1_class)}.{dt1_type}.{priority_token(priority)}.{site}"


def canary_subject(
    *, dt1_class: Dt1Class, dt1_type: str, priority: Priority, site: str
) -> str:
    return f"dt1.canary.{dt1_class_token(dt1_class)}.{dt1_type}.{priority_token(priority)}.{site}"


def ctrl_credits_subject(*, stage_id: str, site: str) -> str:
    return f"ctrl.credits.{stage_id}.{site}"


def ctrl_pressure_subject(*, stage_id: str, site: str) -> str:
    return f"ctrl.pressure.{stage_id}.{site}"


def ctrl_admission_subject(*, ingress_id: str, site: str) -> str:
    return f"ctrl.admission.{ingress_id}.{site}"


@dataclass(frozen=True)
class Dt1JetStreamStreamConfig:
    """
    Spec-3 stream taxonomy as configuration records.

    Note: This is *not* JetStream API objects; it is used to feed adapter code.
    """

    name: str
    subjects: tuple[str, ...]
    retention: str
    storage: str
    max_age_seconds: int


def default_stream_configs(
    *, max_age_dt1_seconds: int = 1800
) -> tuple[Dt1JetStreamStreamConfig, ...]:
    """
    MVP stream defaults aligned with Spec-3 Appendix E.

    - DT1_LIVE: dt1.*.*.*.*
    - DT1_DLQ: dt1.dlq.>
    - DT1_CANARY: dt1.canary.>
    - CTRL: ctrl.>
    """
    return (
        Dt1JetStreamStreamConfig(
            name="DT1_LIVE",
            subjects=("dt1.*.*.*.*",),
            retention="limits",
            storage="file",
            max_age_seconds=max_age_dt1_seconds,
        ),
        Dt1JetStreamStreamConfig(
            name="DT1_DLQ",
            subjects=("dt1.dlq.>",),
            retention="workqueue",
            storage="file",
            max_age_seconds=14 * 24 * 3600,
        ),
        Dt1JetStreamStreamConfig(
            name="DT1_CANARY",
            subjects=("dt1.canary.>",),
            retention="limits",
            storage="file",
            max_age_seconds=60 * 60,
        ),
        Dt1JetStreamStreamConfig(
            name="CTRL",
            subjects=("ctrl.>",),
            retention="limits",
            storage="memory",
            max_age_seconds=60 * 60,
        ),
    )
