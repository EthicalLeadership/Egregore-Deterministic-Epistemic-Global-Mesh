from egregore.dt1 import Dt1Class, Priority
from egregore.dt1.subjects import (
    canary_subject,
    ctrl_admission_subject,
    ctrl_credits_subject,
    ctrl_pressure_subject,
    dlq_subject,
    dt1_class_token,
    priority_token,
    workunit_subject,
)


def test_tokens_are_stable():
    assert dt1_class_token(Dt1Class.DT1_CLASS_L) == "L"
    assert dt1_class_token(Dt1Class.DT1_CLASS_H) == "H"
    assert dt1_class_token(Dt1Class.DT1_CLASS_UNSPECIFIED) == "U"

    assert priority_token(Priority.P0) == "p0"
    assert priority_token(Priority.P7) == "p7"


def test_subject_taxonomy_workunit_dlq_canary():
    s = workunit_subject(
        dt1_class=Dt1Class.DT1_CLASS_L,
        dt1_type="A",
        priority=Priority.P3,
        site="mtl01",
    )
    assert s == "dt1.L.A.p3.mtl01"

    dlq = dlq_subject(
        dt1_class=Dt1Class.DT1_CLASS_H,
        dt1_type="C",
        priority=Priority.P0,
        site="mtl01",
    )
    assert dlq == "dt1.dlq.H.C.p0.mtl01"

    canary = canary_subject(
        dt1_class=Dt1Class.DT1_CLASS_H,
        dt1_type="C",
        priority=Priority.P0,
        site="mtl01",
    )
    assert canary == "dt1.canary.H.C.p0.mtl01"


def test_subject_taxonomy_ctrl():
    assert (
        ctrl_credits_subject(stage_id="cqb", site="mtl01") == "ctrl.credits.cqb.mtl01"
    )
    assert (
        ctrl_pressure_subject(stage_id="cqb", site="mtl01") == "ctrl.pressure.cqb.mtl01"
    )
    assert (
        ctrl_admission_subject(ingress_id="eig1", site="mtl01")
        == "ctrl.admission.eig1.mtl01"
    )
