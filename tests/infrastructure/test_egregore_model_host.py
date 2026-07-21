"""Tests for EgregoreModelHost."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from egregore.infrastructure.egregore_model_host import EgregoreModelHost
from egregore.infrastructure.gguf_catalog import GGUFCatalog, GGUFEntry
from egregore.interface.model_host_ports import InferenceRequest


def test_host_lists_models_from_catalog() -> None:
    catalog = MagicMock(spec=GGUFCatalog)
    catalog._entries = {
        "model-a": GGUFEntry(
            model_id="model-a",
            filename="a.gguf",
            tier="general",
            quantization="Q4_K_M",
            parameters="1B",
            size_bytes=1,
            sha256="a" * 64,
        ),
    }
    host = EgregoreModelHost(catalog=catalog)
    assert host.list_models() == ["model-a"]


def test_host_unavailable_when_no_verified_models() -> None:
    catalog = MagicMock(spec=GGUFCatalog)
    catalog._entries = {}
    catalog.verify_all.return_value = {}
    host = EgregoreModelHost(catalog=catalog)
    assert host.is_available() is False


def test_get_demand_profile_returns_work_unit_demand() -> None:
    catalog = MagicMock(spec=GGUFCatalog)
    catalog.get.return_value = GGUFEntry(
        model_id="model-a",
        filename="a.gguf",
        tier="general",
        quantization="Q4_K_M",
        parameters="1B",
        size_bytes=1_000_000_000,
        sha256="a" * 64,
    )
    host = EgregoreModelHost(catalog=catalog, default_model_id="model-a")
    request = InferenceRequest(
        model_id="model-a",
        input_data=b"hello",
        max_tokens=256,
        temperature=0.7,
        backend="egregore",
        priority=100,
    )
    demand = host.get_demand_profile(request)
    assert demand.dt.value > 0
    assert demand.tu.value > 0


def test_generate_calls_adapter_and_returns_result() -> None:
    catalog = MagicMock(spec=GGUFCatalog)
    catalog.get.return_value = GGUFEntry(
        model_id="model-a",
        filename="a.gguf",
        tier="general",
        quantization="Q4_K_M",
        parameters="1B",
        size_bytes=1,
        sha256="a" * 64,
    )
    host = EgregoreModelHost(catalog=catalog, default_model_id="model-a")

    with patch(
        "egregore.infrastructure.egregore_model_host.LocalLlmAdapter"
    ) as MockAdapter:  # noqa: N806
        adapter = MagicMock()
        adapter.generate.return_value = {
            "text": "Hi there",
            "model_hash": "hash123",
            "prompt_hash": "p_hash",
            "output_hash": "o_hash",
        }
        MockAdapter.return_value = adapter

        with patch("pathlib.Path.exists", return_value=True):
            request = InferenceRequest(
                model_id="model-a",
                input_data=b"hello",
                max_tokens=128,
                temperature=0.7,
                backend="egregore",
                priority=100,
            )
            result = host.generate(request)

    assert result.output_data == b"Hi there"
    assert result.model_id == "model-a"
    assert result.tokens_generated == 2
