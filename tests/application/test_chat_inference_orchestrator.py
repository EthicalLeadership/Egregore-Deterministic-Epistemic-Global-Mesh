"""Tests for ChatInferenceOrchestrator."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from egregore.application.chat_inference_orchestrator import ChatInferenceOrchestrator
from egregore.domain.units import DT, TU


def test_ask_returns_error_when_no_model_available() -> None:
    with patch(
        "egregore.application.chat_inference_orchestrator.EgregoreModelHost"
    ) as MockHost:  # noqa: N806
        host = MagicMock()
        host.is_available.return_value = False
        MockHost.return_value = host

        orch = ChatInferenceOrchestrator()
        result = orch.ask("hello")

    assert result.ok is False
    assert "unavailable" in result.error.lower()


def test_ask_rejected_when_orchestrator_rejects() -> None:
    with patch(
        "egregore.application.chat_inference_orchestrator.EgregoreModelHost"
    ) as MockHost:  # noqa: N806
        host = MagicMock()
        host.is_available.return_value = True
        host.list_models.return_value = ["model-a"]
        MockHost.return_value = host

        with patch(
            "egregore.application.chat_inference_orchestrator.CapacityOrchestrator"
        ) as MockOrchestrator:  # noqa: N806
            orchestrator = MagicMock()
            from egregore.application.admission_controller import AdmissionDecision
            from egregore.application.placement_policy import PlacementDecision

            orchestrator.schedule_inference.return_value = (
                AdmissionDecision.REJECTED_BACKLOG_EXCEEDED,
                PlacementDecision(n_gpu_layers=0, n_threads=4, reason="CPU-only"),
                None,
            )
            MockOrchestrator.build_default.return_value = orchestrator

            orch = ChatInferenceOrchestrator()
            result = orch.ask("hello")

    assert result.ok is False
    assert "rejected" in result.error.lower()
    assert result.placement_reason == "CPU-only"


def test_ask_returns_generated_text_and_placement() -> None:
    with patch(
        "egregore.application.chat_inference_orchestrator.EgregoreModelHost"
    ) as MockHost:  # noqa: N806
        host = MagicMock()
        host.is_available.return_value = True
        host.list_models.return_value = ["model-a"]
        host.generate.return_value = MagicMock(
            output_data=b"Hello, user!",
            model_id="model-a",
            tokens_generated=3,
            dt_consumed=DT(0.5),
            latency_ms=100.0,
        )
        host.get_demand_profile.return_value = MagicMock(dt=DT(0.5), tu=TU(2))
        MockHost.return_value = host

        with patch(
            "egregore.application.chat_inference_orchestrator.CapacityOrchestrator"
        ) as MockOrchestrator:  # noqa: N806
            from egregore.application.admission_controller import AdmissionDecision
            from egregore.application.placement_policy import PlacementDecision

            orchestrator = MagicMock()
            orchestrator.schedule_inference.return_value = (
                AdmissionDecision.ADMITTED,
                PlacementDecision(
                    n_gpu_layers=-1, n_threads=2, reason="GPU full offload"
                ),
                "wu-123",
            )
            MockOrchestrator.build_default.return_value = orchestrator

            with patch(
                "egregore.application.chat_inference_orchestrator.GGUFCatalog"
            ) as MockCatalog:  # noqa: N806
                catalog = MagicMock()
                catalog.get.return_value = MagicMock(size_bytes=1_000_000_000)
                MockCatalog.return_value = catalog

                orch = ChatInferenceOrchestrator()
                result = orch.ask("hello")

    assert result.ok is True
    assert result.text == "Hello, user!"
    assert result.model_id == "model-a"
    assert result.placement_reason == "GPU full offload"
