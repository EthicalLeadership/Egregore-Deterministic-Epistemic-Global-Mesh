import pytest

from egregore.application.admission_controller import AdmissionDecision
from egregore.application.capacity_orchestrator import CapacityOrchestrator
from egregore.domain.units import DT, TU
from egregore.domain.work_unit import (
    WorkUnit,
    WorkUnitDemand,
    WorkUnitRegistry,
    WorkUnitType,
)
from egregore.interface.model_host_ports import InferenceRequest, InferenceResult
from egregore.interface.placement_policy_ports import PlacementDecision


class FakeModelHost:
    """Minimal fake model host for scheduler inference path tests."""

    def __init__(self, n_gpu_layers: int = -1):
        self._n_gpu_layers = n_gpu_layers

    def is_available(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return ["fake-model"]

    def get_demand_profile(self, request: InferenceRequest):
        return WorkUnitDemand(
            dt=DT(1.0),
            tu=TU(10),
            priority=request.priority,
            max_wait_ms=30_000,
        )

    def generate(
        self, request: InferenceRequest, placement: PlacementDecision | None = None
    ) -> InferenceResult:
        return InferenceResult(
            request_id="r1",
            output_data=b"ok",
            tokens_generated=1,
            dt_consumed=DT(1.0),
            latency_ms=1.0,
            model_id=request.model_id,
        )


@pytest.fixture(autouse=True)
def reset_registry():
    WorkUnitRegistry.unlock()
    WorkUnitRegistry.clear()
    yield
    WorkUnitRegistry.clear()


class TestCapacityOrchestrator:
    def test_build(self):
        orch = CapacityOrchestrator.build_default()
        assert orch is not None
        status = orch.get_capacity_status()
        assert status["available_dt"]["value"] == 8.1

    def test_epoch(self):
        orch = CapacityOrchestrator.build_default()
        orch.start_epoch(timestamp_ns=1000)
        wu = WorkUnit(
            "int-test", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        )
        assert orch.submit_work_unit(wu) == AdmissionDecision.ADMITTED
        log = orch.end_epoch(timestamp_ns=2000)
        assert log["epoch_number"] == 1

    def test_capacity_status(self):
        orch = CapacityOrchestrator.build_default()
        status = orch.get_capacity_status()
        assert status["backlog_count"] == 0
        assert status["epoch_count"] == 0

    def test_schedule_inference_preserves_tu_type_on_gpu_placement(self):
        """Regression: GPU placement used to coerce demand.tu to int, breaking the second call."""
        orch = CapacityOrchestrator.build_default(total_dt=DT(10.0), total_tu=TU(100))
        orch._model_host = FakeModelHost(n_gpu_layers=-1)  # full GPU offload

        req = InferenceRequest(
            model_id="fake-model",
            input_data=b"prompt",
            max_tokens=512,
            temperature=0.0,
            backend="egregore",
            priority=100,
        )

        decision1, placement1, _wu1 = orch.schedule_inference(
            req, model_size_bytes=1_000_000_000
        )
        assert decision1 == AdmissionDecision.ADMITTED
        assert placement1.n_gpu_layers == -1

        # The second submission must not crash TUBudget.remaining with an int TU.
        decision2, _placement2, _wu2 = orch.schedule_inference(
            req, model_size_bytes=1_000_000_000
        )
        assert decision2 == AdmissionDecision.ADMITTED
