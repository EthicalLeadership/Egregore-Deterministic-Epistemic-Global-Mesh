"""Tests for VRAM residency (Phase 6): pre-flight, swap, GGUF backend."""

from __future__ import annotations

from typing import Any

import pytest

from egregore.factory import residency
from egregore.factory.residency import ResidencyManager, VramInsufficientError


# ---------------------------------------------------------------------------
# Pre-flight math
# ---------------------------------------------------------------------------
def test_preflight_passes_with_headroom(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(residency, "vram_free_mb", lambda: 5000)
    mgr = ResidencyManager()
    assert mgr.pre_flight("cnc") == 5000


def test_preflight_blocks_on_shortfall(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(residency, "vram_free_mb", lambda: 91)
    mgr = ResidencyManager()
    with pytest.raises(VramInsufficientError) as exc_info:
        mgr.pre_flight("cnc")
    assert exc_info.value.free_mb == 91
    assert exc_info.value.station == "cnc"
    assert "vram_insufficient" in str(exc_info.value)


def test_preflight_unknown_vram_proceeds(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(residency, "vram_free_mb", lambda: -1)
    mgr = ResidencyManager()
    assert mgr.pre_flight("cnc") == -1  # cannot verify -> proceed, logged


# ---------------------------------------------------------------------------
# Swap protocol
# ---------------------------------------------------------------------------
class FakeGguf:
    def __init__(self) -> None:
        self._loaded = ["my-coder-ft", "qwen-1.5b"]
        self.calls: list[str] = []

    def loaded_models(self) -> list[str]:
        return list(self._loaded)

    def unload_all(self) -> None:
        self.calls.append("unload_all")
        self._loaded = []

    def _get(self, name: str) -> str:
        self.calls.append(f"reload:{name}")
        self._loaded.append(name)
        return name


class FakeHeavy:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_heavy_pass_swap_ordering():
    gguf = FakeGguf()
    order: list[str] = []

    def factory() -> FakeHeavy:
        order.append("load_heavy")
        return FakeHeavy()

    mgr = ResidencyManager(gguf_backend=gguf, heavy_backend_factory=factory)
    with mgr.heavy_pass() as heavy:
        order.append("run")
        assert isinstance(heavy, FakeHeavy)
        assert gguf.loaded_models() == []  # hot residents gone during heavy

    # GGUF unloaded BEFORE heavy load; heavy unloaded; residents restored after
    assert order == ["load_heavy", "run"]
    assert gguf.calls[0] == "unload_all"
    assert heavy.closed is True
    assert sorted(c for c in gguf.calls if c.startswith("reload:")) == [
        "reload:my-coder-ft",
        "reload:qwen-1.5b",
    ]
    assert gguf.loaded_models() == ["my-coder-ft", "qwen-1.5b"]


def test_heavy_pass_restores_residents_on_failure():
    gguf = FakeGguf()

    def factory() -> FakeHeavy:
        return FakeHeavy()

    mgr = ResidencyManager(gguf_backend=gguf, heavy_backend_factory=factory)
    with pytest.raises(RuntimeError, match="boom"), mgr.heavy_pass():
        raise RuntimeError("boom")
    # Residents restored despite the failure
    assert gguf.loaded_models() == ["my-coder-ft", "qwen-1.5b"]
    assert mgr._heavy is None


def test_heavy_pass_serializes(monkeypatch: pytest.MonkeyPatch):
    """Two overlapping heavy passes are impossible (lock held)."""
    gguf = FakeGguf()
    mgr = ResidencyManager(gguf_backend=gguf, heavy_backend_factory=FakeHeavy)
    acquired = mgr._swap_lock.acquire(blocking=False)
    assert acquired
    try:
        assert not mgr._swap_lock.acquire(blocking=False)
    finally:
        mgr._swap_lock.release()


# ---------------------------------------------------------------------------
# GGUF backend (mocked llama)
# ---------------------------------------------------------------------------
class FakeLlama:
    def create_chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "gguf says hi"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        }


@pytest.fixture
def gguf_backend(monkeypatch: pytest.MonkeyPatch):
    from egregore.infrastructure.gguf_backend import GgufBackend

    backend = GgufBackend(models={"my-coder-ft": "/fake/7b.gguf", "qwen-1.5b": "/fake/1b.gguf"})
    monkeypatch.setattr(backend, "_load", lambda model: FakeLlama())
    return backend


def _chat_request(model: str) -> Any:
    from egregore.domain.inference_models import ChatMessage, ChatRequest

    return ChatRequest(
        model=model,
        messages=[ChatMessage(role="user", content="hi")],
        max_tokens=8,
    )


def test_gguf_chat_contract(gguf_backend):
    resp = gguf_backend.chat(_chat_request("my-coder-ft"))
    assert resp.message.content == "gguf says hi"
    assert resp.usage == {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}
    assert resp.finish_reason == "stop"


def test_gguf_prefix_stripping(gguf_backend):
    """gguf-my-coder-ft routes here AND resolves to the my-coder-ft GGUF."""
    resp = gguf_backend.chat(_chat_request("gguf-my-coder-ft"))
    assert resp.message.content == "gguf says hi"
    assert gguf_backend.loaded_models() == ["my-coder-ft"]  # stripped lookup


def test_gguf_lazy_load_and_unload(gguf_backend):
    assert gguf_backend.loaded_models() == []
    gguf_backend.chat(_chat_request("qwen-1.5b"))
    assert gguf_backend.loaded_models() == ["qwen-1.5b"]
    gguf_backend.unload("qwen-1.5b")
    assert gguf_backend.loaded_models() == []


def test_gguf_unknown_model_fails(gguf_backend):
    with pytest.raises(RuntimeError, match="no model 'nope'"):
        gguf_backend.chat(_chat_request("nope"))


def test_gguf_model_registry(gguf_backend):
    models = {m["id"] for m in gguf_backend.list_models()}
    assert models == {"my-coder-ft", "qwen-1.5b"}
    assert gguf_backend.model_exists("my-coder-ft")
    assert not gguf_backend.model_exists("nope")
    assert gguf_backend.health()


# ---------------------------------------------------------------------------
# Backend routing
# ---------------------------------------------------------------------------
def test_resolve_backend_routes_gguf_prefix():
    from egregore.application.inference_service import _resolve_backend

    assert _resolve_backend("gguf-my-coder-ft") == "gguf"
    assert _resolve_backend("gguf-qwen-1.5b") == "gguf"
    assert _resolve_backend("my-coder-ft") == "egregore"  # unchanged
